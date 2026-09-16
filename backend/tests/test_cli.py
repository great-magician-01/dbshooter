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


# ── M2: query / export / history ──

def test_query_table(cli, sqlite_conn_id):
    r = cli.invoke(app, ['query', sqlite_conn_id, 'select id, name from users order by id',
                         '--format', 'table'])
    assert r.exit_code == 0, r.output
    assert '张三' in r.output and '王五' in r.output and '共 3 行' in r.output


def test_query_json(cli, sqlite_conn_id):
    import json
    r = cli.invoke(app, ['query', sqlite_conn_id, 'select name from users order by id',
                         '--format', 'json'])
    assert r.exit_code == 0, r.output
    results = json.loads(r.output)
    assert results[0]['rows'][0] == ['张三']


def test_query_csv_and_raw(cli, sqlite_conn_id):
    r = cli.invoke(app, ['query', sqlite_conn_id, 'select name from users order by id',
                         '--format', 'csv'])
    assert r.exit_code == 0, r.output
    assert r.output.splitlines()[0] == 'name'
    r = cli.invoke(app, ['query', sqlite_conn_id, 'select city from users where id=1',
                         '--format', 'raw'])
    assert r.exit_code == 0, r.output
    assert r.output.strip() == '上海'


def test_query_affected(cli, sqlite_conn_id):
    r = cli.invoke(app, ['query', sqlite_conn_id,
                         "insert into users(name, city) values ('赵六','杭州')",
                         '--format', 'json'])
    assert r.exit_code == 0, r.output
    assert '"affected": 1' in r.output


def test_query_error_exit1(cli, sqlite_conn_id):
    r = cli.invoke(app, ['query', sqlite_conn_id, 'select * from nope'])
    assert r.exit_code == 1
    assert 'nope' in r.output


def test_query_readonly_blocked(cli, sqlite_db):
    """只读拦截由服务端兜底,CLI 原样透出错误并以 1 退出。"""
    import uuid
    name = f'ro-{uuid.uuid4().hex[:6]}'   # test_api.py 也建过名为 ro 的连接,避开
    r = cli.invoke(app, ['conn', 'add', '--name', name, '--type', 'sqlite', '--readonly',
                         '--param', f'path={sqlite_db}'])
    assert r.exit_code == 0, r.output
    r = cli.invoke(app, ['query', name, "insert into users(name) values ('x')"])
    assert r.exit_code == 1
    assert '只读' in r.output


def test_query_from_file_and_stdin(cli, sqlite_conn_id, tmp_path):
    f = tmp_path / 'q.sql'
    f.write_text('select count(*) as n from users', encoding='utf-8')
    r = cli.invoke(app, ['query', sqlite_conn_id, '-f', str(f), '--format', 'raw'])
    assert r.exit_code == 0, r.output
    assert r.output.strip() == '3'
    r = cli.invoke(app, ['query', sqlite_conn_id, '--stdin', '--format', 'raw'],
                   input='select count(*) from v_users')
    assert r.exit_code == 0, r.output
    assert r.output.strip() == '3'


def test_query_out_csv(cli, sqlite_conn_id, tmp_path):
    out = tmp_path / 'o.csv'
    r = cli.invoke(app, ['query', sqlite_conn_id, 'select * from users order by id',
                         '-o', str(out)])
    assert r.exit_code == 0, r.output
    text = out.read_text(encoding='utf-8-sig')
    assert text.splitlines()[0] == 'id,name,city' and '张三' in text


def test_export(cli, sqlite_conn_id, tmp_path):
    out = tmp_path / 'big.csv'
    r = cli.invoke(app, ['export', sqlite_conn_id, 'select * from users', '-o', str(out)])
    assert r.exit_code == 0, r.output
    text = out.read_text(encoding='utf-8-sig')
    assert text.splitlines()[0] == 'id,name,city'
    assert len(text.splitlines()) == 4


def test_history(cli, sqlite_conn_id):
    r = cli.invoke(app, ['query', sqlite_conn_id, 'select 1'])
    assert r.exit_code == 0, r.output
    r = cli.invoke(app, ['history', '--format', 'json'])
    assert r.exit_code == 0, r.output
    import json
    items = json.loads(r.output)
    assert any(h['stmt'] == 'select 1' and h['status'] == 'done' for h in items)
