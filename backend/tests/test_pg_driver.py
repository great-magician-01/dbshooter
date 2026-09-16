"""PG 驱动执行路径单测:不连真实库,用假 asyncpg 对象锁定调用约定。

回归点:PreparedStatement.fetch(*args) 的位置参数是查询参数而非行数,
有结果集的语句必须走事务内的游标按 limit+1 截断读取。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.drivers.base import QueryError
from backend.app.drivers.pg_driver import PgDriver


class _FakeAttr:
    def __init__(self, name: str, oid: int):
        self.name = name
        self.type = SimpleNamespace(oid=oid)


class _FakeCursor:
    """ps.cursor() 返回的可 await 对象;fetch(n) 的位置参数是行数。"""

    def __init__(self, recs: list[dict]):
        self._recs = list(recs)
        self.fetch_sizes: list[int] = []

    def __await__(self):
        async def _self():
            return self
        return _self().__await__()

    async def fetch(self, n: int):
        self.fetch_sizes.append(n)
        out, self._recs = self._recs[:n], self._recs[n:]
        return out


class _FakePrepared:
    def __init__(self, attrs: list, recs: list[dict]):
        self._attrs = attrs
        self._recs = recs
        self.last_cursor: _FakeCursor | None = None

    def get_attributes(self):
        return self._attrs

    def cursor(self):
        self.last_cursor = _FakeCursor(self._recs)
        return self.last_cursor

    async def fetch(self, *args, **kwargs):
        raise AssertionError('应走游标读取,不应直接调用 PreparedStatement.fetch(*args)')


class _FakeConn:
    def __init__(self, prepared: _FakePrepared):
        self._prepared = prepared
        self.executed: list[str] = []
        self.tx_count = 0

    async def prepare(self, stmt: str):
        return self._prepared

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self):
                conn.tx_count += 1
                return self

            async def __aexit__(self, *exc):
                return False

        return _Tx()

    async def execute(self, stmt: str):
        self.executed.append(stmt)
        return 'UPDATE 3'


class _FakePool:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def acquire(self):
        pool = self

        class _Acq:
            async def __aenter__(self):
                return pool._conn

            async def __aexit__(self, *exc):
                return False

        return _Acq()


def _driver(conn: _FakeConn, **cfg) -> PgDriver:
    d = PgDriver({'type': 'pg', **cfg})
    d.pool = _FakePool(conn)  # pool 非 None 时 connect() 跳过真实建连
    return d


async def test_rows走游标并截断():
    attrs = [_FakeAttr('id', 23), _FakeAttr('name', 25)]
    recs = [{'id': i, 'name': f'n{i}'} for i in range(4)]
    prepared = _FakePrepared(attrs, recs)
    conn = _FakeConn(prepared)

    res = (await _driver(conn).execute('SELECT * FROM t', limit=3))[0]

    assert res.kind == 'rows' and res.truncated is True
    assert res.rows == [[0, 'n0'], [1, 'n1'], [2, 'n2']]
    assert res.columns == [{'name': 'id', 'type': 'int4'}, {'name': 'name', 'type': 'text'}]
    assert prepared.last_cursor is not None
    assert prepared.last_cursor.fetch_sizes == [4]      # limit + 1
    assert conn.tx_count == 1                            # 游标在事务内创建


async def test_无结果集走execute报affected():
    conn = _FakeConn(_FakePrepared([], []))

    res = (await _driver(conn).execute('UPDATE t SET a = 1'))[0]

    assert res.kind == 'affected' and res.affected == 3
    assert conn.executed == ['UPDATE t SET a = 1']
    assert conn.tx_count == 0


async def test_只读模式拦截写语句():
    conn = _FakeConn(_FakePrepared([], []))

    with pytest.raises(QueryError):
        await _driver(conn, readonly=True).execute('DELETE FROM t')
