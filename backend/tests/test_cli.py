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


# ── M3: ai ──

def test_ws_url():
    from backend.cli.wsclient import ws_url
    assert ws_url('http://127.0.0.1:5718', None) == 'ws://127.0.0.1:5718/ws'
    assert ws_url('https://example.com/', 'tk') == 'wss://example.com/ws?token=tk'


def _fake_ai_events(events):
    """生成 stream_ai_events 桩:events 为 (event, data) 列表。"""
    def fake(url, payload, timeout):
        yield from events
    return fake


def test_ai_ask_streams(cli, sqlite_conn_id, monkeypatch):
    import backend.cli.commands.ai as ai_cmd
    monkeypatch.setattr(ai_cmd, 'stream_ai_events', _fake_ai_events([
        ('ai.started', {}),
        ('ai.tool', {'name': 'list_tables', 'args': '{}', 'status': 'done', 'summary': '3 张表'}),
        ('ai.token', {'delta': '查询'}), ('ai.token', {'delta': '如下'}),
        ('ai.done', {'text': '查询如下', 'sql': 'SELECT * FROM users', 'elapsed_ms': 12}),
    ]))
    r = cli.invoke(app, ['ai', 'ask', sqlite_conn_id, '查所有用户'])
    assert r.exit_code == 0, r.output
    assert '查询如下' in r.output and 'SELECT * FROM users' in r.output
    # 自动建会话,且标题取问题前缀
    import json
    sessions = json.loads(cli.invoke(app, ['ai', 'sessions', 'list', '--format', 'json']).output)
    assert any(s['title'].startswith('查所有用户') for s in sessions)


def test_ai_ask_sql_only(cli, sqlite_conn_id, monkeypatch):
    import backend.cli.commands.ai as ai_cmd
    monkeypatch.setattr(ai_cmd, 'stream_ai_events', _fake_ai_events([
        ('ai.token', {'delta': '一些解释文字'}),
        ('ai.done', {'text': '一些解释文字', 'sql': 'SELECT 1', 'elapsed_ms': 1}),
    ]))
    r = cli.invoke(app, ['ai', 'ask', sqlite_conn_id, 'q', '--sql'])
    assert r.exit_code == 0, r.output
    assert r.output.strip() == 'SELECT 1'


def test_ai_ask_error(cli, sqlite_conn_id, monkeypatch):
    import backend.cli.commands.ai as ai_cmd
    monkeypatch.setattr(ai_cmd, 'stream_ai_events', _fake_ai_events([
        ('ai.error', {'message': '尚未配置生效的 AI Provider'}),
    ]))
    r = cli.invoke(app, ['ai', 'ask', sqlite_conn_id, 'q'])
    assert r.exit_code == 1
    assert '尚未配置' in r.output


def test_ai_providers_crud(cli):
    r = cli.invoke(app, ['ai', 'providers', 'add', '--name', 'DeepSeek',
                         '--base-url', 'https://api.deepseek.com/v1', '--model', 'deepseek-chat',
                         '--api-key', 'sk-x', '--activate'])
    assert r.exit_code == 0, r.output
    r = cli.invoke(app, ['ai', 'providers', 'list'])
    assert r.exit_code == 0, r.output
    assert 'DeepSeek' in r.output and 'deepseek-chat' in r.output
    import json
    items = json.loads(cli.invoke(app, ['ai', 'providers', 'list', '--format', 'json']).output)
    pid = next(p for p in items if p['name'] == 'DeepSeek')['id']
    assert pid  # activate 已生效(不重复断言状态字段,行为由服务端测试覆盖)
    r = cli.invoke(app, ['ai', 'providers', 'activate', pid[:8]])
    assert r.exit_code == 0, r.output


def test_ai_providers_test_fails_gracefully(cli):
    """指向不可达地址的 provider,test 命令业务失败退出码 1。"""
    r = cli.invoke(app, ['ai', 'providers', 'add', '--name', 'bad',
                         '--base-url', 'http://127.0.0.1:9/v1', '--model', 'm',
                         '--api-key', 'k'])
    assert r.exit_code == 0, r.output
    import json
    items = json.loads(cli.invoke(app, ['ai', 'providers', 'list', '--format', 'json']).output)
    pid = next(p for p in items if p['name'] == 'bad')['id']
    r = cli.invoke(app, ['ai', 'providers', 'test', pid[:8]])
    assert r.exit_code == 1
    assert '连接失败' in r.output
