"""MySQL 驱动:fake aiomysql 池验证结构/关系元数据解析,不连真实 MySQL。"""
from __future__ import annotations

from typing import Any

import aiomysql
import pytest

from backend.app.drivers.mysql_driver import MysqlDriver

# information_schema.COLUMNS 罐装行(按驱动 SELECT 列序)
_COLUMN_ROWS = [
    ('id', 'int', 'NO', None, 'PRI', 1, ''),
    ('name', 'varchar(64)', 'YES', None, '', 2, '姓名'),
    ('amount', 'decimal(10,2)', 'YES', '0.00', '', 3, ''),
]

# information_schema.KEY_COLUMN_USAGE 罐装行(ORDINAL_POSITION 1 起)
_FK_ROWS = [
    ('fk_orders_user', 'shop', 'orders', 'user_id', 'shop', 'users', 'id', 1),
    ('fk_emp_mgr', 'shop', 'employees', 'manager_id', 'shop', 'employees', 'id', 1),
    ('fk_children', 'shop', 'children', 'x', 'shop', 'parents', 'a', 1),
    ('fk_children', 'shop', 'children', 'y', 'shop', 'parents', 'b', 2),
    # 跨库引用(users 的入站)
    ('fk_cross', 'other', 't1', 'uid', 'shop', 'users', 'id', 1),
]


class _Cursor:
    """记录 SQL,按 information_schema 目标表路由罐装行。"""

    def __init__(self, conn: '_Conn'):
        self.conn = conn
        self._rows: list[tuple[Any, ...]] = []

    async def __aenter__(self) -> '_Cursor':
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, sql: str, args: tuple[Any, ...] | None = None) -> None:
        self.conn.log.append((sql, args))
        if 'information_schema.COLUMNS' in sql:
            self._rows = list(_COLUMN_ROWS)
        elif 'information_schema.KEY_COLUMN_USAGE' in sql and args:
            # 复刻 WHERE:双侧 (db, table) 过滤 + 约束名/序号排序
            db, table, ref_db, ref_table = args
            rows = [r for r in _FK_ROWS
                    if (r[1], r[2]) == (db, table) or (r[4], r[5]) == (ref_db, ref_table)]
            self._rows = sorted(rows, key=lambda r: (r[0], r[7]))
        else:
            self._rows = []

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self):
        self.log: list[tuple[str, tuple[Any, ...] | None]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)


class _Pool:
    def __init__(self):
        self.conn = _Conn()

    def acquire(self):
        pool = self

        class _Acq:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *exc: Any) -> bool:
                return False
        return _Acq()


@pytest.fixture()
def mysql(monkeypatch) -> MysqlDriver:
    async def fake_create_pool(**kwargs: Any):
        return _Pool()

    monkeypatch.setattr(aiomysql, 'create_pool', fake_create_pool)
    return MysqlDriver({'type': 'mysql', 'database': 'shop'})


def _log(mysql: MysqlDriver) -> list[tuple[str, tuple[Any, ...] | None]]:
    assert mysql.pool is not None
    from typing import cast
    return cast(_Pool, mysql.pool).conn.log


async def test_table_columns(mysql: MysqlDriver):
    """结构页签:COLUMN_TYPE 全型/可空/默认值/注释;pk=COLUMN_KEY='PRI'。"""
    cols = await mysql.table_columns('shop.users')
    assert [c.name for c in cols] == ['id', 'name', 'amount']
    id_col, name_col, amount_col = cols
    assert id_col.pk is True and id_col.nullable is False and id_col.ordinal == 1
    assert name_col.type == 'varchar(64)' and name_col.comment == '姓名'
    assert amount_col.default == '0.00' and amount_col.pk is False
    sql, args = _log(mysql)[0]
    assert 'COLUMN_COMMENT' in sql and args == ('shop', 'users')


async def test_table_relations_bidirectional(mysql: MysqlDriver):
    """users:orders 的普通入站 + 跨库入站(other.t1);WHERE 双侧过滤。"""
    rels = await mysql.table_relations('shop.users')
    assert {r.name for r in rels} == {'fk_orders_user', 'fk_cross'}
    assert all(r.direction == 'in' for r in rels)
    cross = next(r for r in rels if r.name == 'fk_cross')
    assert cross.schema == 'other' and cross.table == 't1'
    assert cross.ref_schema == 'shop' and cross.ref_table == 'users'
    sql, args = _log(mysql)[0]
    assert 'TABLE_NAME' in sql and 'REFERENCED_TABLE_NAME' in sql
    assert args == ('shop', 'users', 'shop', 'users')


async def test_table_relations_self_reference(mysql: MysqlDriver):
    """自引用同时命中双侧条件,只记一条 'out'。"""
    rels = await mysql.table_relations('shop.employees')
    assert len(rels) == 1
    rel = rels[0]
    assert rel.direction == 'out' and rel.table == 'employees'
    assert rel.ref_table == 'employees' and rel.ref_column == 'id'


async def test_table_relations_composite(mysql: MysqlDriver):
    """复合 FK:同名两行,ORDINAL_POSITION 1 起归一为 0 起;出站方向。"""
    rels = await mysql.table_relations('shop.children')
    assert len(rels) == 2
    by_seq = {r.seq: r for r in rels}
    assert by_seq[0].column == 'x' and by_seq[0].ref_column == 'a'
    assert by_seq[1].column == 'y' and by_seq[1].ref_column == 'b'
    assert all(r.direction == 'out' and r.name == 'fk_children' for r in rels)


async def test_path_without_db_segment(mysql: MysqlDriver):
    """单段路径回退到连接绑定的 database。"""
    cols = await mysql.table_columns('users')
    assert [c.name for c in cols] == ['id', 'name', 'amount']
    _, args = _log(mysql)[0]
    assert args == ('shop', 'users')
