"""CLI 测试:TestClient 直插 ApiClient,走真实路由但不起真实端口。

cli fixture 把 cli.state.make_client 替换为指向内存 app 的客户端,命令层零改动。
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from backend.cli.client import ApiClient
from backend.cli.main import app


@pytest.fixture()
def cli(client, monkeypatch) -> CliRunner:
    """把 CLI 的服务端指向内存中的 app(conftest 的 TestClient)。"""
    import backend.cli.state as cli_state
    monkeypatch.setattr(cli_state, 'make_client', lambda *a, **k: ApiClient(client))
    return CliRunner()


def _add_sqlite(cli: CliRunner, sqlite_db: str, name: str = '本地库', exact: bool = False) -> str:
    """建 sqlite 连接并返回实际名称。元数据库全会话共享,默认带随机后缀避免跨用例撞名。"""
    import uuid
    real = name if exact else f'{name}-{uuid.uuid4().hex[:6]}'
    r = cli.invoke(app, ['conn', 'add', '--name', real, '--type', 'sqlite',
                         '--param', f'path={sqlite_db}'])
    assert r.exit_code == 0, r.output
    return real


def test_health(cli):
    r = cli.invoke(app, ['health'])
    assert r.exit_code == 0, r.output
    assert 'sqlite' in r.output


def test_conn_add_and_list(cli, sqlite_db):
    name = _add_sqlite(cli, sqlite_db)
    r = cli.invoke(app, ['conn', 'list'])
    assert r.exit_code == 0, r.output
    assert name in r.output and 'sqlite' in r.output
    r = cli.invoke(app, ['conn', 'list', '--format', 'json'])
    assert r.exit_code == 0 and name in r.output


def test_conn_resolve_by_name_and_test(cli, sqlite_db):
    name = _add_sqlite(cli, sqlite_db)
    r = cli.invoke(app, ['conn', 'test', name])
    assert r.exit_code == 0, r.output
    assert '连接成功' in r.output


def test_conn_resolve_by_id_prefix(cli, sqlite_db):
    _add_sqlite(cli, sqlite_db)
    items = cli.invoke(app, ['conn', 'list', '--format', 'json']).output
    import json
    cid = json.loads(items)[0]['id']
    r = cli.invoke(app, ['conn', 'test', cid[:8]])
    assert r.exit_code == 0, r.output


def test_conn_resolve_ambiguous(cli, sqlite_db):
    import uuid
    dup = f'重名-{uuid.uuid4().hex[:6]}'
    _add_sqlite(cli, sqlite_db, name=dup, exact=True)
    _add_sqlite(cli, sqlite_db, name=dup, exact=True)
    r = cli.invoke(app, ['conn', 'test', dup])
    assert r.exit_code == 1
    assert '匹配到多个' in r.output


def test_conn_resolve_not_found(cli):
    r = cli.invoke(app, ['conn', 'test', '不存在'])
    assert r.exit_code == 1
    assert '找不到连接' in r.output


def test_conn_test_inline_config(cli, sqlite_db):
    r = cli.invoke(app, ['conn', 'test', '--type', 'sqlite', '--param', f'path={sqlite_db}'])
    assert r.exit_code == 0, r.output
    assert '连接成功' in r.output


def test_conn_test_bad_config_fails(cli, tmp_path):
    r = cli.invoke(app, ['conn', 'test', '--type', 'sqlite',
                         '--param', f'path={tmp_path}/nope/x.db'])
    assert r.exit_code == 1
    assert '连接失败' in r.output


def test_conn_delete(cli, sqlite_db):
    name = _add_sqlite(cli, sqlite_db)
    r = cli.invoke(app, ['conn', 'delete', name, '-y'])
    assert r.exit_code == 0, r.output
    assert name not in cli.invoke(app, ['conn', 'list']).output


def test_tree_depth2(cli, sqlite_db, sqlite_conn_id):
    r = cli.invoke(app, ['tree', sqlite_conn_id, '--depth', '2'])
    assert r.exit_code == 0, r.output
    assert 'main/' in r.output and 'users' in r.output and 'v_users' in r.output


def test_tree_json(cli, sqlite_conn_id):
    r = cli.invoke(app, ['tree', sqlite_conn_id, '--format', 'json'])
    assert r.exit_code == 0, r.output
    assert '"main"' in r.output


def test_ddl(cli, sqlite_conn_id):
    r = cli.invoke(app, ['ddl', sqlite_conn_id, 'users', 'v_users'])
    assert r.exit_code == 0, r.output
    assert 'CREATE TABLE users' in r.output and 'CREATE VIEW v_users' in r.output


def test_ddl_missing_table(cli, sqlite_conn_id):
    r = cli.invoke(app, ['ddl', sqlite_conn_id, 'nope'])
    assert r.exit_code == 1
