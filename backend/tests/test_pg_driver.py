"""PG 驱动:fake asyncpg 池验证 schema 绑定(SET LOCAL search_path),不连真实 PG。"""
from __future__ import annotations

from typing import Any, cast

import asyncpg
import pytest

from backend.app.drivers.pg_driver import PgDriver

# 无结果集的语句首词(prepare 后走 conn.execute 记影响行数)
_WRITE_HEADS = ('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'ALTER', 'SET', 'GRANT')


class _Type:
    def __init__(self, oid: int):
        self.oid = oid


class _Attr:
    def __init__(self, name: str):
        self.name = name
        self.type = _Type(23)


class _Cursor:
    def __init__(self, recs: list):
        self._recs = recs

    async def fetch(self, n: int) -> list:
        return self._recs[:n]


class _PS:
    """prepare 的产物:写语句无属性,查询返回一列;含 nope 的语句抛错。"""

    def __init__(self, sql: str):
        self.sql = sql

    def get_attributes(self) -> list:
        head = self.sql.lstrip().upper()
        return [] if head.startswith(_WRITE_HEADS) else [_Attr('id')]

    async def cursor(self) -> _Cursor:
        return _Cursor([{'id': 1}, {'id': 2}])


class _Conn:
    """记录每条 SQL 执行时的事务深度,用于断言 SET LOCAL 是否包在事务内。"""

    def __init__(self):
        self.log: list[tuple[str, int, str]] = []
        self.depth = 0

    def transaction(self):
        conn = self

        class _Txn:
            async def __aenter__(self):
                conn.depth += 1

            async def __aexit__(self, *exc):
                conn.depth -= 1
                return False
        return _Txn()

    async def execute(self, sql: str) -> str:
        self.log.append(('execute', self.depth, sql))
        return 'UPDATE 1'

    async def prepare(self, sql: str) -> _PS:
        self.log.append(('prepare', self.depth, sql))
        if 'nope' in sql:
            raise Exception('relation "nope" does not exist')
        return _PS(sql)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.log.append(('fetchval', self.depth, sql))
        if 'relkind' in sql:
            # v_users 罐装为视图,其余为普通表
            return 'v' if args and args[1] == 'v_users' else 'r'
        if 'pg_get_viewdef' in sql:
            return 'SELECT id, name FROM public.users'
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        """结构/关系元数据查询的罐装返回(按 SQL 子串路由)。"""
        self.log.append(('fetch', self.depth, sql))
        if 'pg_attribute a' in sql and 'format_type' in sql:
            return [
                {'name': 'id', 'type': 'integer', 'nullable': False,
                 'default': "nextval('users_id_seq'::regclass)", 'ordinal': 1,
                 'comment': None},
                {'name': 'name', 'type': 'character varying(64)', 'nullable': True,
                 'default': None, 'ordinal': 2, 'comment': '姓名'},
            ]
        if 'pg_index i' in sql:
            return [{'attname': 'id'}]
        if 'pg_constraint con' in sql:
            # 复刻 WHERE:双侧 (schema, table) 过滤 + conname/序号排序
            schema, table = args[0], args[1]
            rows = [r for r in _FK_ROWS
                    if (r['src_schema'], r['src_table']) == (schema, table)
                    or (r['ref_schema'], r['ref_table']) == (schema, table)]
            return sorted(rows, key=lambda r: (r['name'], r['seq']))
        return []


# pg_constraint 罐装数据(seq 按 PG WITH ORDINALITY 口径 1 起)
_FK_ROWS = [
    {'name': 'fk_orders_user', 'src_schema': 'public', 'src_table': 'orders',
     'src_column': 'user_id', 'ref_schema': 'public', 'ref_table': 'users',
     'ref_column': 'id', 'seq': 1},
    {'name': 'fk_emp_mgr', 'src_schema': 'public', 'src_table': 'employees',
     'src_column': 'manager_id', 'ref_schema': 'public', 'ref_table': 'employees',
     'ref_column': 'id', 'seq': 1},
    {'name': 'fk_children', 'src_schema': 'public', 'src_table': 'children',
     'src_column': 'x', 'ref_schema': 'public', 'ref_table': 'parents',
     'ref_column': 'a', 'seq': 1},
    {'name': 'fk_children', 'src_schema': 'public', 'src_table': 'children',
     'src_column': 'y', 'ref_schema': 'public', 'ref_table': 'parents',
     'ref_column': 'b', 'seq': 2},
    # 跨 schema 引用(users 的入站)
    {'name': 'fk_cross', 'src_schema': 'sales', 'src_table': 't1',
     'src_column': 'uid', 'ref_schema': 'public', 'ref_table': 'users',
     'ref_column': 'id', 'seq': 1},
]


class _Pool:
    def __init__(self):
        self.conn = _Conn()

    def acquire(self):
        pool = self

        class _Acq:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *exc):
                return False
        return _Acq()


@pytest.fixture()
def pg(monkeypatch) -> PgDriver:
    async def fake_create_pool(**kwargs):
        return _Pool()

    monkeypatch.setattr(asyncpg, 'create_pool', fake_create_pool)
    return PgDriver({'type': 'pg', 'database': 'demo'})


def _conn(pg: PgDriver) -> _Conn:
    """取 fake 池里的连接(运行时 pool 已被 monkeypatch 成 _Pool,声明类型仍是 asyncpg.Pool)。"""
    assert pg.pool is not None
    return cast(_Pool, pg.pool).conn


async def test_schema_binding(pg: PgDriver):
    res = await pg.execute('SELECT * FROM users', schema='sales')
    assert res[0].kind == 'rows' and res[0].rows == [[1], [2]]
    log = _conn(pg).log
    # SET LOCAL 在事务内执行,标识符加引号
    assert ('execute', 1, 'SET LOCAL search_path TO "sales"') in log
    # 用户语句在同一事务内 prepare(search_path 已生效)
    prep = next(e for e in log if e[0] == 'prepare')
    assert prep == ('prepare', 1, 'SELECT * FROM users')


async def test_schema_identifier_quoting(pg: PgDriver):
    await pg.execute('SELECT 1', schema='a"b')
    assert ('execute', 1, 'SET LOCAL search_path TO "a""b"') in _conn(pg).log


async def test_without_schema_no_set(pg: PgDriver):
    await pg.execute('SELECT 1')
    assert all('search_path' not in e[2] for e in _conn(pg).log)
    # 无绑定时语句不在外层事务内(prepare 深度为 0)
    assert _conn(pg).log[0] == ('prepare', 0, 'SELECT 1')


async def test_schema_write_statement(pg: PgDriver):
    res = await pg.execute('UPDATE users SET name = 1', schema='sales')
    assert res[0].kind == 'affected' and res[0].affected == 1
    executes = [e for e in _conn(pg).log if e[0] == 'execute']
    # SET LOCAL 先于用户语句执行
    assert executes[0][2] == 'SET LOCAL search_path TO "sales"'
    assert executes[1][2] == 'UPDATE users SET name = 1'


async def test_schema_error_isolated(pg: PgDriver):
    """绑定 schema 时单条语句报错只回滚自身,后续语句照常执行。"""
    res = await pg.execute('SELECT * FROM nope; SELECT 1', schema='sales')
    assert res[0].kind == 'error' and 'nope' in (res[0].error or '')
    assert res[1].kind == 'rows'
    # 两条语句各注入一次 search_path
    sets = [e for e in _conn(pg).log if e[2].startswith('SET LOCAL')]
    assert len(sets) == 2
    # 出错后事务深度归零,未污染下一条语句的执行环境
    assert _conn(pg).depth == 0


async def test_readonly_server_settings(monkeypatch):
    """只读连接在服务端开会话级只读:数据修改 CTE / EXPLAIN ANALYZE DML 一并拦截。"""
    captured: dict[str, Any] = {}

    async def fake_create_pool(**kwargs: Any):
        captured.update(kwargs)
        return _Pool()

    monkeypatch.setattr(asyncpg, 'create_pool', fake_create_pool)
    d = PgDriver({'type': 'pg', 'database': 'demo', 'readonly': True})
    await d.connect()
    assert captured['server_settings'] == {'default_transaction_read_only': 'on'}

    captured.clear()
    d2 = PgDriver({'type': 'pg', 'database': 'demo'})
    await d2.connect()
    assert 'server_settings' not in captured  # 普通连接不受影响


async def test_table_columns(pg: PgDriver):
    """结构页签:pg_attribute 全型 + 注释 + PK merge;序号 1 起。"""
    cols = await pg.table_columns('demo.public.users')
    assert [c.name for c in cols] == ['id', 'name']
    id_col, name_col = cols
    assert id_col.pk is True and id_col.nullable is False and id_col.ordinal == 1
    assert id_col.default == "nextval('users_id_seq'::regclass)"
    assert name_col.type == 'character varying(64)' and name_col.comment == '姓名'
    assert name_col.pk is False
    sql = next(e[2] for e in _conn(pg).log if e[0] == 'fetch')
    assert 'pg_attribute' in sql and 'col_description' in sql


async def test_table_relations_bidirectional(pg: PgDriver):
    """users:orders 的普通入站 + 跨 schema 入站(sales.t1)。"""
    rels = await pg.table_relations('demo.public.users')
    assert {r.name for r in rels} == {'fk_orders_user', 'fk_cross'}
    assert all(r.direction == 'in' for r in rels)
    cross = next(r for r in rels if r.name == 'fk_cross')
    assert cross.schema == 'sales' and cross.table == 't1' and cross.column == 'uid'
    assert cross.ref_schema == 'public' and cross.ref_table == 'users'
    sql = next(e[2] for e in _conn(pg).log if e[0] == 'fetch')
    assert "contype = 'f'" in sql


async def test_table_relations_self_reference(pg: PgDriver):
    """自引用同时命中双侧条件,只记一条 'out'。"""
    rels = await pg.table_relations('demo.public.employees')
    assert len(rels) == 1
    rel = rels[0]
    assert rel.direction == 'out' and rel.table == 'employees'
    assert rel.ref_table == 'employees' and rel.ref_column == 'id'


async def test_table_relations_composite(pg: PgDriver):
    """复合 FK:同名两行,PG 序号 1 起归一为 0 起;出站方向。"""
    rels = await pg.table_relations('demo.public.children')
    assert len(rels) == 2
    by_seq = {r.seq: r for r in rels}
    assert by_seq[0].column == 'x' and by_seq[0].ref_column == 'a'
    assert by_seq[1].column == 'y' and by_seq[1].ref_column == 'b'
    assert all(r.direction == 'out' and r.name == 'fk_children' for r in rels)


async def test_ddl_synthesis(pg: PgDriver):
    """DDL 页签/AI 上下文:合成 CREATE TABLE 含 NOT NULL/DEFAULT/PK/FK。"""
    ddl = await pg.ddl(['demo.public.orders'])
    assert 'CREATE TABLE public.orders' in ddl
    assert '"id" integer DEFAULT nextval' in ddl and 'NOT NULL' in ddl
    assert 'PRIMARY KEY ("id")' in ddl
    assert ('CONSTRAINT "fk_orders_user" FOREIGN KEY ("user_id")'
            ' REFERENCES public.users ("id")') in ddl


async def test_ddl_view_uses_viewdef(pg: PgDriver):
    """视图不合成 CREATE TABLE,走 pg_get_viewdef 输出真实定义。"""
    ddl = await pg.ddl(['demo.public.v_users'])
    assert ddl.startswith('CREATE VIEW public.v_users AS')
    assert 'SELECT id, name FROM public.users' in ddl
    assert 'CREATE TABLE' not in ddl
