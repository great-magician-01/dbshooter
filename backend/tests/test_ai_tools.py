"""AI 自助查表工具:真实 sqlite 驱动验证 list/describe,monkeypatch 验证 pg/mysql 命名空间。"""
from __future__ import annotations

from backend.app.drivers.base import MetaNode
from backend.app.drivers.mysql_driver import MysqlDriver
from backend.app.drivers.pg_driver import PgDriver
from backend.app.drivers.sqlite_driver import SqliteDriver
from backend.app.services import ai_tools


def _sqlite(path: str) -> SqliteDriver:
    return SqliteDriver({'type': 'sqlite', 'params': {'path': path}})


async def test_sqlite_namespaces_and_tables(sqlite_db):
    d = _sqlite(sqlite_db)
    assert await d.ai_namespaces() == ['main']
    labels = [n.label for n in await d.ai_tables()]
    assert 'users' in labels and 'v_users' in labels
    await d.close()


async def test_list_tables_sqlite(sqlite_db):
    d = _sqlite(sqlite_db)
    res = await ai_tools.run_tool(d, 'list_tables', {})
    assert res.ok and 'users' in res.content and 'v_users (view)' in res.content
    assert res.summary
    await d.close()


async def test_describe_table_sqlite(sqlite_db):
    d = _sqlite(sqlite_db)
    res = await ai_tools.run_tool(d, 'describe_table', {'table': 'users'})
    assert res.ok and 'CREATE TABLE users' in res.content
    assert res.summary == '查看 users 表结构'
    await d.close()


async def test_describe_table_not_found(sqlite_db):
    d = _sqlite(sqlite_db)
    res = await ai_tools.run_tool(d, 'describe_table', {'table': 'nope'})
    assert not res.ok and '未找到表' in res.content
    await d.close()


async def test_describe_table_rejects_bad_identifier(sqlite_db):
    d = _sqlite(sqlite_db)
    for bad in ('users; DROP TABLE users', '', 'a b', 'users--'):
        res = await ai_tools.run_tool(d, 'describe_table', {'table': bad})
        assert not res.ok and '非法表名' in res.content, bad
    await d.close()


async def test_unknown_tool(sqlite_db):
    d = _sqlite(sqlite_db)
    res = await ai_tools.run_tool(d, 'drop_table', {})
    assert not res.ok and '未知工具' in res.content
    await d.close()


async def test_pg_namespace_prefix(monkeypatch):
    """PG 的 ai_tables 必须拼 current_db 前缀(ai_tables/ai_namespaces 委托 metadata)。"""
    d = PgDriver({'type': 'pg', 'database': 'shop'})
    seen: list[str] = []

    async def fake_metadata(self, path: str) -> list[MetaNode]:
        seen.append(path)
        return []

    monkeypatch.setattr(PgDriver, 'metadata', fake_metadata)
    assert await d.ai_namespaces() == []
    await d.ai_tables('orders')
    await d.ai_tables()
    assert seen == ['shop', 'shop.orders', 'shop.public']


async def test_pg_describe_bare_name_fallback(monkeypatch):
    """裸表名在 public 落空时,找到唯一同名 schema 后用限定名重试。"""
    d = PgDriver({'type': 'pg', 'database': 'shop'})
    ddls: list[str] = []

    async def fake_namespaces(self) -> list[str]:
        return ['public', 'orders']

    async def fake_tables(self, ns: str | None = None) -> list[MetaNode]:
        return [MetaNode(path='p', label='users', kind='table')] if ns == 'orders' else []

    async def fake_ddl(self, tables: list[str]) -> str:
        ddls.append(tables[0])
        return 'CREATE TABLE orders.users(id int);' if tables[0] == 'orders.users' else ''

    monkeypatch.setattr(PgDriver, 'ai_namespaces', fake_namespaces)
    monkeypatch.setattr(PgDriver, 'ai_tables', fake_tables)
    monkeypatch.setattr(PgDriver, 'ddl', fake_ddl)

    res = await ai_tools.run_tool(d, 'describe_table', {'table': 'users'})
    assert res.ok and 'orders.users' in res.content
    assert res.summary == '查看 orders.users 表结构'
    assert ddls == ['users', 'orders.users']


async def test_pg_describe_bare_name_ambiguous(monkeypatch):
    """多个 schema 同名时不猜,把候选限定名返回给模型自选。"""
    d = PgDriver({'type': 'pg', 'database': 'shop'})

    async def fake_namespaces(self) -> list[str]:
        return ['public', 'orders']

    async def fake_tables(self, ns: str | None = None) -> list[MetaNode]:
        return [MetaNode(path='p', label='users', kind='table')]

    async def fake_ddl(self, tables: list[str]) -> str:
        return ''

    monkeypatch.setattr(PgDriver, 'ai_namespaces', fake_namespaces)
    monkeypatch.setattr(PgDriver, 'ai_tables', fake_tables)
    monkeypatch.setattr(PgDriver, 'ddl', fake_ddl)

    res = await ai_tools.run_tool(d, 'describe_table', {'table': 'users'})
    assert not res.ok
    assert 'public.users' in res.content and 'orders.users' in res.content


async def test_pg_list_tables_grouped(monkeypatch):
    """多 schema 时按组输出 'schema (n 张): …'。"""
    d = PgDriver({'type': 'pg', 'database': 'shop'})

    async def fake_namespaces(self) -> list[str]:
        return ['public', 'orders']

    async def fake_tables(self, ns: str | None = None) -> list[MetaNode]:
        return [MetaNode(path='p', label=f'{ns}_t1', kind='table')]

    monkeypatch.setattr(PgDriver, 'ai_namespaces', fake_namespaces)
    monkeypatch.setattr(PgDriver, 'ai_tables', fake_tables)

    res = await ai_tools.run_tool(d, 'list_tables', {})
    assert res.ok
    assert 'public (1 张): public_t1' in res.content
    assert 'orders (1 张): orders_t1' in res.content


async def test_mysql_system_dbs_filtered(monkeypatch):
    """未绑库时 ai_namespaces 排除 information_schema/mysql/sys 等系统库。"""
    d = MysqlDriver({'type': 'mysql'})

    async def fake_metadata(self, path: str) -> list[MetaNode]:
        assert path == ''
        return [MetaNode(path=n, label=n, kind='database', has_children=True)
                for n in ('information_schema', 'mysql', 'performance_schema', 'sys', 'shop')]

    monkeypatch.setattr(MysqlDriver, 'metadata', fake_metadata)
    assert await d.ai_namespaces() == ['shop']


async def test_mysql_bound_db_single_namespace():
    """绑了库的连接只看当前库,且不触发任何 metadata 查询。"""
    d = MysqlDriver({'type': 'mysql', 'database': 'shop'})
    assert await d.ai_namespaces() == ['shop']
