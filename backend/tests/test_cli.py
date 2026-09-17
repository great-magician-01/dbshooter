"""CLI 测试:TestClient 直插 ApiClient,走真实路由但不起真实端口。

cli fixture 把 cli.state.make_client 替换为指向内存 app 的客户端,命令层零改动。
"""
from __future__ import annotations

import httpx
import pytest
from typer.testing import CliRunner

from backend.cli.client import ApiClient
from backend.cli.errors import EXIT_UNAUTHORIZED, EXIT_UNREACHABLE, CliError
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
    import json
    name = _add_sqlite(cli, sqlite_db)
    items = json.loads(cli.invoke(app, ['conn', 'list', '--format', 'json']).output)
    # 元数据库会话共享且 created_at 秒精度,不能假定 [0] 是刚建的,按名找回
    cid = next(c['id'] for c in items if c['name'] == name)
    r = cli.invoke(app, ['conn', 'test', cid[:8]])
    assert r.exit_code == 0, r.output


def test_conn_update(cli, sqlite_db):
    import json
    name = _add_sqlite(cli, sqlite_db)
    r = cli.invoke(app, ['conn', 'update', name, '--name', f'{name}-改', '--readonly'])
    assert r.exit_code == 0, r.output
    items = json.loads(cli.invoke(app, ['conn', 'list', '--format', 'json']).output)
    row = next(c for c in items if c['name'] == f'{name}-改')
    assert row['readonly'] is True
    assert row['params']['path'] == sqlite_db   # 未传的字段保持原值
    r = cli.invoke(app, ['query', f'{name}-改', "insert into users(name) values ('x')"])
    assert r.exit_code == 1 and '只读' in r.output   # readonly 生效


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
    import uuid
    pname = f'DS-{uuid.uuid4().hex[:6]}'   # test_api.py 也建过 DeepSeek,避开重名
    r = cli.invoke(app, ['ai', 'providers', 'add', '--name', pname,
                         '--base-url', 'https://api.deepseek.com/v1', '--model', 'deepseek-chat',
                         '--api-key', 'sk-x', '--activate'])
    assert r.exit_code == 0, r.output
    r = cli.invoke(app, ['ai', 'providers', 'list'])
    assert r.exit_code == 0, r.output
    assert pname in r.output and 'deepseek-chat' in r.output
    import json
    items = json.loads(cli.invoke(app, ['ai', 'providers', 'list', '--format', 'json']).output)
    mine = next(p for p in items if p['name'] == pname)
    assert mine['is_active']   # add --activate 已生效
    pid = mine['id']
    r = cli.invoke(app, ['ai', 'providers', 'activate', pid[:8]])
    assert r.exit_code == 0, r.output


def test_ai_providers_test_fails_gracefully(cli):
    """指向不可达地址的 provider,test 命令业务失败退出码 1。"""
    import uuid
    bad = f'bad-{uuid.uuid4().hex[:6]}'
    r = cli.invoke(app, ['ai', 'providers', 'add', '--name', bad,
                         '--base-url', 'http://127.0.0.1:9/v1', '--model', 'm',
                         '--api-key', 'k'])
    assert r.exit_code == 0, r.output
    import json
    items = json.loads(cli.invoke(app, ['ai', 'providers', 'list', '--format', 'json']).output)
    pid = next(p for p in items if p['name'] == bad)['id']
    r = cli.invoke(app, ['ai', 'providers', 'test', pid[:8]])
    assert r.exit_code == 1
    assert '连接失败' in r.output


# ── M4: settings / serve ──

def test_settings_roundtrip(cli):
    import uuid
    k = f'cli-key-{uuid.uuid4().hex[:6]}'
    r = cli.invoke(app, ['settings', 'set', k, 'v1'])
    assert r.exit_code == 0, r.output
    r = cli.invoke(app, ['settings', 'get', k])
    assert r.exit_code == 0 and r.output.strip() == 'v1'
    r = cli.invoke(app, ['settings', 'get', '--format', 'json'])
    assert r.exit_code == 0 and k in r.output
    r = cli.invoke(app, ['settings', 'get', '不存在的键'])
    assert r.exit_code == 1


def test_serve_registered():
    """serve 是阻塞命令不进真跑,验证已注册(直接查注册表,help 里 --server 会误命中 'serve')。"""
    names = [c.name for c in app.registered_commands]
    assert 'serve' in names and 'query' in names and 'export' in names


# ── review 修复回归 ──

def test_ws_url_token_quoted():
    """token 含 +/& 必须 URL 编码,否则服务端 parse_qsl 还原错误。"""
    from backend.cli.wsclient import ws_url
    assert ws_url('http://h:1', 'a+b&c=d') == 'ws://h:1/ws?token=a%2Bb%26c%3Dd'


def test_command_kind_csv_not_empty(cli, sqlite_conn_id, capsys):
    """redis 类 command 结果在 csv/raw 下也要有输出(管道默认 csv,不能静默空)。"""
    from backend.cli.output import print_results
    results = [{'kind': 'command', 'columns': [{'name': 'count', 'type': 'int'}],
                'rows': [[3]], 'raw': 3}]
    print_results(results, 'csv', None)
    out = capsys.readouterr().out
    assert 'count' in out and '3' in out
    print_results(results, 'raw', None)
    assert '3' in capsys.readouterr().out


def test_query_file_gbk_and_bom(cli, sqlite_db, tmp_path):
    """GBK 文件与 UTF-8-BOM 文件都能正确读入(BOM 不得触发只读误判)。"""
    import uuid
    name = f'ro-{uuid.uuid4().hex[:6]}'
    assert cli.invoke(app, ['conn', 'add', '--name', name, '--type', 'sqlite', '--readonly',
                            '--param', f'path={sqlite_db}']).exit_code == 0
    gbk = tmp_path / 'gbk.sql'
    gbk.write_bytes("select name from users where city='上海'".encode('gbk'))
    r = cli.invoke(app, ['query', name, '-f', str(gbk), '--format', 'raw'])
    assert r.exit_code == 0, r.output
    assert '张三' in r.output
    bom = tmp_path / 'bom.sql'
    bom.write_bytes('﻿select 1'.encode('utf-8'))
    r = cli.invoke(app, ['query', name, '-f', str(bom), '--format', 'raw'])
    assert r.exit_code == 0, r.output   # 只读连接下仍放行 → BOM 已剥掉
    assert r.output.strip() == '1'


def test_query_missing_file_no_traceback(cli, sqlite_conn_id):
    """文件不存在:业务失败退出码 1,不抛裸 traceback。"""
    r = cli.invoke(app, ['query', sqlite_conn_id, '-f', 'no-such-file.sql'])
    assert r.exit_code == 1
    assert 'Traceback' not in r.output


def test_export_has_bom(cli, sqlite_conn_id, tmp_path):
    """export 与 query -o 统一带 BOM(Excel 直开不乱码)。"""
    out = tmp_path / 'big.csv'
    r = cli.invoke(app, ['export', sqlite_conn_id, 'select * from users', '-o', str(out)])
    assert r.exit_code == 0, r.output
    assert out.read_bytes().startswith(b'\xef\xbb\xbf')


# ── 二次审查修复回归 ──

_STUB_CONN = {'id': 'a1b2c3d4-1111-2222-3333-444455556666',
              'name': 'stub-conn', 'type': 'redis', 'params': {}}


def _stub_cli(monkeypatch, handler) -> CliRunner:
    """把 CLI 指向 httpx.MockTransport 伪服务端:验证传输层错误与结果渲染,不起真实 app。"""
    import backend.cli.state as cli_state
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(cli_state, 'make_client',
                        lambda *a, **k: ApiClient(httpx.Client(transport=transport,
                                                               base_url='http://stub')))
    return CliRunner()


def _add_readonly_sqlite(cli: CliRunner, sqlite_db: str) -> str:
    """建只读 sqlite 连接(只读拦截在服务端,CLI 只透出错误)。"""
    import uuid
    name = f'ro-{uuid.uuid4().hex[:6]}'
    r = cli.invoke(app, ['conn', 'add', '--name', name, '--type', 'sqlite', '--readonly',
                         '--param', f'path={sqlite_db}'])
    assert r.exit_code == 0, r.output
    return name


def test_query_empty_ref_rejected(cli):
    """空连接引用:startswith('') 恒真,单连接时曾静默落到唯一连接上执行。"""
    r = cli.invoke(app, ['query', '', 'select 1'])
    assert r.exit_code == 1
    assert '请提供连接' in r.output
    r = cli.invoke(app, ['query', '   ', 'select 1'])
    assert r.exit_code == 1
    assert '请提供连接' in r.output


def test_query_stdin_bom_not_blocked(cli, sqlite_db):
    """--stdin 带 UTF-8 BOM:首词不能被 BOM 污染,否则只读连接会误拦成写操作。"""
    name = _add_readonly_sqlite(cli, sqlite_db)
    r = cli.invoke(app, ['query', name, '--stdin', '--format', 'raw'], input='\ufeffselect 1')
    assert r.exit_code == 0, r.output
    assert r.output.strip() == '1'


def test_query_multi_statement_all_results(cli, sqlite_conn_id, tmp_path):
    """多语句:csv/raw/-o 都要输出全部结果集,不能只取第一条(静默丢数据)。"""
    r = cli.invoke(app, ['query', sqlite_conn_id, 'select 1 as a; select 2 as b',
                         '--format', 'csv'])
    assert r.exit_code == 0, r.output
    assert [ln for ln in r.output.splitlines() if ln] == ['a', '1', 'b', '2']
    r = cli.invoke(app, ['query', sqlite_conn_id, 'select 1 as a; select 2 as b',
                         '--format', 'raw'])
    assert r.exit_code == 0, r.output
    assert [ln for ln in r.output.splitlines() if ln] == ['1', '2']
    out = tmp_path / 'multi.csv'
    r = cli.invoke(app, ['query', sqlite_conn_id, 'select 1 as a; select 2 as b',
                         '-o', str(out)])
    assert r.exit_code == 0, r.output
    assert [ln for ln in out.read_text(encoding='utf-8-sig').splitlines() if ln] \
        == ['a', '1', 'b', '2']


def test_query_command_result_to_csv(monkeypatch, tmp_path):
    """redis command 结果 -o 导 csv:kind=command 也是结果集,不再误报"查询无结果集"。"""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == '/api/connections':
            return httpx.Response(200, json={'items': [_STUB_CONN]})
        return httpx.Response(200, json={'results': [
            {'kind': 'command', 'columns': [{'name': 'GET', 'type': ''}],
             'rows': [['hello']], 'raw': 'hello', 'elapsed_ms': 1}]})

    cli = _stub_cli(monkeypatch, handler)
    out = tmp_path / 'r.csv'
    r = cli.invoke(app, ['query', 'stub-conn', 'GET k', '-o', str(out)])
    assert r.exit_code == 0, r.output
    assert [ln for ln in out.read_text(encoding='utf-8-sig').splitlines() if ln] \
        == ['GET', 'hello']


def test_query_documents_without_columns(monkeypatch, tmp_path):
    """mongo aggregate(columns=[]、数据在 raw):csv 输出 JSONL,table 逐条 JSON。"""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == '/api/connections':
            return httpx.Response(200, json={'items': [_STUB_CONN]})
        return httpx.Response(200, json={'results': [
            {'kind': 'documents', 'columns': [], 'rows': [],
             'raw': [{'a': 1}, {'a': 2}], 'elapsed_ms': 3}]})

    cli = _stub_cli(monkeypatch, handler)
    r = cli.invoke(app, ['query', 'stub-conn', 'db.c.aggregate([])', '--format', 'csv'])
    assert r.exit_code == 0, r.output
    assert [ln for ln in r.output.splitlines() if ln] == ['{"a": 1}', '{"a": 2}']
    r = cli.invoke(app, ['query', 'stub-conn', 'db.c.aggregate([])', '--format', 'raw'])
    assert r.exit_code == 0, r.output
    assert [ln for ln in r.output.splitlines() if ln] == ['{"a": 1}', '{"a": 2}']
    r = cli.invoke(app, ['query', 'stub-conn', 'db.c.aggregate([])', '--format', 'table'])
    assert r.exit_code == 0, r.output
    assert '"a": 1' in r.output and '共 2 条' in r.output
    out = tmp_path / 'docs.csv'
    r = cli.invoke(app, ['query', 'stub-conn', 'db.c.aggregate([])', '-o', str(out)])
    assert r.exit_code == 0, r.output
    assert '{"a": 1}' in out.read_text(encoding='utf-8-sig')


def test_non_json_response_exit3(monkeypatch):
    """200 但响应不是 JSON(指到了别的 web 服务)→ 退出码 3,无裸 traceback。"""
    cli = _stub_cli(monkeypatch, lambda req: httpx.Response(200, text='<html>not dbs</html>'))
    r = cli.invoke(app, ['health'])
    assert r.exit_code == EXIT_UNREACHABLE
    assert '不是 JSON' in r.output and 'Traceback' not in r.output


def test_rest_unauthorized_exit4(cli, monkeypatch):
    """服务端开启令牌校验且请求不带令牌 → 退出码 4。"""
    import backend.app.config as app_config
    monkeypatch.setattr(app_config, 'ACCESS_TOKEN', 'secret-token')
    r = cli.invoke(app, ['health'])
    assert r.exit_code == EXIT_UNAUTHORIZED
    assert '未授权' in r.output and 'Traceback' not in r.output


def _invalid_status(code: int):
    """构造 websockets 的握手被拒异常(服务端在 accept 前返回非 101)。"""
    from websockets.datastructures import Headers
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response as WsResponse
    return InvalidStatus(WsResponse(code, 'Nope', Headers(), b''))


def test_ws_handshake_error_hides_token(monkeypatch):
    """握手被拒(非 401/403)时:错误信息不得带 ?token= 明文,退出码为 3。"""
    import backend.cli.wsclient as wsc

    def boom(*args, **kwargs):
        raise _invalid_status(404)

    monkeypatch.setattr(wsc, '_ws_connect', boom)
    with pytest.raises(CliError) as ei:
        list(wsc.stream_ai_events('ws://h:1/ws?token=SECRET', {}, 1.0))
    assert ei.value.code == EXIT_UNREACHABLE
    assert '404' in ei.value.message
    assert 'SECRET' not in ei.value.message and 'token' not in ei.value.message


def test_ws_handshake_403_is_unauthorized(monkeypatch):
    """握手被拒且状态是 403(服务端令牌校验失败)→ 退出码 4。"""
    import backend.cli.wsclient as wsc

    def boom(*args, **kwargs):
        raise _invalid_status(403)

    monkeypatch.setattr(wsc, '_ws_connect', boom)
    with pytest.raises(CliError) as ei:
        list(wsc.stream_ai_events('ws://h:1/ws?token=SECRET', {}, 1.0))
    assert ei.value.code == EXIT_UNAUTHORIZED
    assert 'SECRET' not in ei.value.message


def test_ai_ask_ws_failure_cleans_session(cli, sqlite_conn_id, monkeypatch):
    """WS 建不起来时,自动新建的会话要补偿删除(不留孤儿会话)。"""
    import json

    import backend.cli.commands.ai as ai_cmd

    def boom(url, payload, timeout):
        raise CliError('无法连接或响应超时:确认服务已启动', EXIT_UNREACHABLE)
        yield   # 生成器占位:异常在首次迭代时抛出,与真实 stream_ai_events 一致

    monkeypatch.setattr(ai_cmd, 'stream_ai_events', boom)
    r = cli.invoke(app, ['ai', 'ask', sqlite_conn_id, '孤儿会话问题'])
    assert r.exit_code == EXIT_UNREACHABLE
    sessions = json.loads(cli.invoke(app, ['ai', 'sessions', 'list', '--format', 'json']).output)
    assert not any(s['title'].startswith('孤儿会话问题') for s in sessions)


def test_ai_providers_add_without_key_non_interactive(cli):
    """非交互环境不给 --api-key:不能弹提示 Abort,应留空并提示。"""
    import uuid
    name = f'nokey-{uuid.uuid4().hex[:6]}'
    r = cli.invoke(app, ['ai', 'providers', 'add', '--name', name,
                         '--base-url', 'http://127.0.0.1:9/v1', '--model', 'm'])
    assert r.exit_code == 0, r.output
    assert '已创建' in r.output and '跳过 API Key' in r.output


def test_conn_add_rejects_unknown_type(monkeypatch):
    """--type 写错:本地白名单直接报错,压根不发请求(伪服务端一律 500 兜底)。"""
    cli = _stub_cli(monkeypatch, lambda req: httpx.Response(500, json={'detail': '不该发请求'}))
    r = cli.invoke(app, ['conn', 'add', '--name', 'x', '--type', 'postgresql'])
    assert r.exit_code == 1
    assert '不支持的数据库类型' in r.output and 'postgresql' in r.output


def test_limit_local_validation(cli, sqlite_conn_id):
    """limit 超服务端上限:本地中文报错并提示上限,不等 422。"""
    r = cli.invoke(app, ['query', sqlite_conn_id, 'select 1', '--limit', '6000'])
    assert r.exit_code == 1
    assert '1~5000' in r.output
    r = cli.invoke(app, ['query', sqlite_conn_id, 'select 1', '--limit', '0'])
    assert r.exit_code == 1
    assert '1~5000' in r.output
    r = cli.invoke(app, ['export', sqlite_conn_id, 'select 1', '--limit', '60000'])
    assert r.exit_code == 1
    assert '1~50000' in r.output


def test_export_from_file_schema_and_stdin(cli, sqlite_conn_id, tmp_path):
    """export 支持 -f/--stdin 与 --schema 透传(与 query 同一套读数逻辑)。"""
    f = tmp_path / 'e.sql'
    f.write_text('select name from users order by id', encoding='utf-8')
    out = tmp_path / 'from_file.csv'
    r = cli.invoke(app, ['export', sqlite_conn_id, '-f', str(f), '-o', str(out),
                         '--schema', 'main'])
    assert r.exit_code == 0, r.output
    assert out.read_text(encoding='utf-8-sig').splitlines() == ['name', '张三', '李四', '王五']
    out2 = tmp_path / 'from_stdin.csv'
    r = cli.invoke(app, ['export', sqlite_conn_id, '--stdin', '-o', str(out2)],
                   input='select city from users where id=1')
    assert r.exit_code == 0, r.output
    assert out2.read_text(encoding='utf-8-sig').splitlines() == ['city', '上海']


def test_table_cell_markup_rendered_literally(capsys):
    """数据里的 rich markup 必须原样显示(单元格用 Text 包裹),不能被吞字/建超链接。"""
    from rich.console import Console

    from backend.cli.output import print_results
    results = [{'kind': 'rows', 'columns': [{'name': 'v', 'type': ''}],
                'rows': [['[b]hi[/b]'], ['[link=http://evil]点我[/link]']]}]
    print_results(results, 'table', Console(no_color=True, highlight=False))
    out = capsys.readouterr().out
    assert '[b]hi[/b]' in out and '[link=http://evil]点我[/link]' in out


def test_error_and_cells_escape_control_chars(capsys):
    """库数据里的 ESC 等控制字符转义显示,不能改终端状态。"""
    from rich.console import Console

    from backend.cli.output import print_results
    results = [{'kind': 'rows', 'columns': [{'name': 'v', 'type': ''}],
                'rows': [['\x1b[31mred\x1b[0m', 'ok']]},
               {'kind': 'rows', 'error': '\x1b]0;标题\x07'}]
    print_results(results, 'table', Console(no_color=True, highlight=False))
    out = capsys.readouterr().out
    assert '\x1b' not in out and '\x07' not in out
    assert '\\x1b[31mred' in out and '\\x07' in out


# ── 三轮审查修复回归 ──

def test_csv_formula_injection_prefixed():
    """CSV 公式注入防护:字符串以 =/+/-/@ 开头时加 ' 前缀(与服务端 _csv_safe 同口径)。"""
    # 前缀集合的权威定义在服务端 backend/app/api/query.py::_csv_safe,改一处要同步另一处
    import io

    from backend.cli.output import write_results_csv
    results = [{'kind': 'rows', 'columns': [{'name': '=cmd', 'type': ''}],
                'rows': [['=1+1'], ['+A1'], ['-2+3'], ['@SUM(A1)'], ['ok']],
                'elapsed_ms': 1}]
    buf = io.StringIO()
    assert write_results_csv(results, buf) == 5
    lines = buf.getvalue().splitlines()
    assert lines[0] == "'=cmd"                       # 列名同样按数据处理
    assert lines[1:] == ["'=1+1", "'+A1", "'-2+3", "'@SUM(A1)", 'ok']


def test_csv_tab_cr_leading_value_prefixed():
    """\t / \r 引导的单元格 Excel 会跳过引导继续解析公式,前缀集须与服务端 _csv_safe 同步。"""
    import csv
    import io

    from backend.cli.output import write_results_csv
    results = [{'kind': 'rows', 'columns': [{'name': '\t=列名', 'type': ''}],
                'rows': [['\t=cmd'], ['\r@x'], ['\ttext']]}]
    buf = io.StringIO()
    write_results_csv(results, buf)
    rows = list(csv.reader(io.StringIO(buf.getvalue())))
    assert rows[0] == ["'\t=列名"]
    assert rows[1] == ["'\t=cmd"] and rows[2] == ["'\r@x"]
    assert rows[3] == ["'\ttext"]      # 服务端同口径:只看首字符,不做语义判断


def test_csv_negative_number_not_prefixed():
    """数值 -5 文本化后首字符也是 '-',但它不是公式,加前缀会改坏数据。"""
    import io

    from backend.cli.output import write_results_csv
    results = [{'kind': 'rows', 'columns': [{'name': 'n', 'type': ''}],
                'rows': [[-5], [-0.5], [{'a': 1}]]}]
    buf = io.StringIO()
    write_results_csv(results, buf)
    assert buf.getvalue().splitlines()[1:] == ['-5', '-0.5', '"{""a"": 1}"']


def test_query_csv_injection_through_cli(monkeypatch, tmp_path):
    """--format csv 与 -o 两条路径都要中和公式(Excel 直开可执行的风险)。"""
    import csv
    import io

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == '/api/connections':
            return httpx.Response(200, json={'items': [_STUB_CONN]})
        return httpx.Response(200, json={'results': [
            {'kind': 'rows', 'columns': [{'name': 'v', 'type': ''}],
             'rows': [['=HYPERLINK("http://evil","点我")'], ['text']], 'elapsed_ms': 1}]})

    cli = _stub_cli(monkeypatch, handler)
    r = cli.invoke(app, ['query', 'stub-conn', 'q', '--format', 'csv'])
    assert r.exit_code == 0, r.output
    assert '=HYPERLINK' in r.output and "'=HYPERLINK" in r.output
    out = tmp_path / 'inj.csv'
    r = cli.invoke(app, ['query', 'stub-conn', 'q', '-o', str(out)])
    assert r.exit_code == 0, r.output
    rows = list(csv.reader(io.StringIO(out.read_text(encoding='utf-8-sig'))))
    assert rows[0] == ['v'] and rows[1][0].startswith("'=HYPERLINK") and rows[2] == ['text']


def test_ws_invalid_port_no_traceback(monkeypatch):
    """端口写成非数字:parse_uri 抛 ValueError(不是 WebSocketException 子类),转 CliError。"""
    import backend.cli.wsclient as wsc

    def boom(*args, **kwargs):
        raise ValueError("Port could not be cast to integer value as '57l8'")

    monkeypatch.setattr(wsc, '_ws_connect', boom)
    with pytest.raises(CliError) as ei:
        list(wsc.stream_ai_events('ws://h:57l8/ws?token=SECRET', {}, 1.0))
    assert ei.value.code == EXIT_UNREACHABLE
    assert '57l8' in ei.value.message and 'SECRET' not in ei.value.message


def test_ws_real_parse_uri_value_error(monkeypatch):
    """不桩化 _ws_connect,直接喂真地址:websockets 内部同样以 ValueError 收场。"""
    import backend.cli.wsclient as wsc
    with pytest.raises(CliError) as ei:
        list(wsc.stream_ai_events('ws://127.0.0.1:57l8/ws?token=SECRET', {}, 1.0))
    assert ei.value.code == EXIT_UNREACHABLE
    assert 'SECRET' not in ei.value.message


def test_ws_exception_message_token_masked(monkeypatch):
    """WebSocketException 的文本可能带原始 uri(InvalidURI 等),必须脱敏后再打。"""
    from websockets.exceptions import InvalidURI

    import backend.cli.wsclient as wsc

    def boom(*args, **kwargs):
        # websockets 17 的 InvalidURI 会把 uri 原样写进 __str__(真实路径:scheme 不是 ws/wss)
        raise InvalidURI('ws://h:1/ws?token=SECRET', "scheme isn't ws or wss")

    monkeypatch.setattr(wsc, '_ws_connect', boom)
    with pytest.raises(CliError) as ei:
        list(wsc.stream_ai_events('ws://h:1/ws?token=SECRET', {}, 1.0))
    assert ei.value.code == EXIT_UNREACHABLE
    assert 'SECRET' not in ei.value.message and '?token' not in ei.value.message


def test_ws_refused_masks_token(monkeypatch):
    """连不上服务时提示里的地址同样不能带查询串。"""
    import backend.cli.wsclient as wsc

    def boom(*args, **kwargs):
        raise ConnectionRefusedError('refused')

    monkeypatch.setattr(wsc, '_ws_connect', boom)
    with pytest.raises(CliError) as ei:
        list(wsc.stream_ai_events('ws://h:1/ws?token=SECRET', {}, 1.0))
    assert ei.value.code == EXIT_UNREACHABLE
    assert 'ws://h:1/ws' in ei.value.message and 'SECRET' not in ei.value.message


def test_invalid_server_url_exit1_no_traceback():
    """-s 端口非数字:httpx.InvalidURL 不是 HTTPError 子类,须转中文错误、退出码 1、无 traceback。"""
    r = CliRunner().invoke(app, ['-s', 'http://127.0.0.1:57l8', 'health'])
    assert r.exit_code == 1
    assert '无效的服务地址' in r.output and 'Traceback' not in r.output


def test_mask_url_strips_query_and_userinfo():
    """报错信息里的地址脱敏:REST 与 WS 共用 errors.mask_url,剥查询串(token)与 userinfo。"""
    from backend.cli.errors import mask_url
    assert mask_url('http://h:1/?token=SECRET&a=1') == 'http://h:1/'
    assert mask_url('http://u:p@h:1/dbs') == 'http://h:1/dbs'
    assert mask_url('ws://h:1/ws?token=SECRET') == 'ws://h:1/ws'
    assert mask_url('http://h:1/dbs') == 'http://h:1/dbs'      # 无敏感段时原样
    assert mask_url('http://127.0.0.1:5718') == 'http://127.0.0.1:5718'


def test_rest_unreachable_hint_masks_token():
    """-s 把令牌写进 URL 且服务连不上:提示里的地址不得带 token(退出码 3、无 traceback)。"""
    r = CliRunner().invoke(app, ['-s', 'http://127.0.0.1:1/?token=SECRET', 'health'])
    assert r.exit_code == EXIT_UNREACHABLE
    assert 'Traceback' not in r.output
    assert 'SECRET' not in r.output and 'token' not in r.output
    assert 'http://127.0.0.1:1' in r.output


def test_rest_unreachable_hint_masks_userinfo():
    """userinfo 形式(http://user:pass@host)的凭据同样不能进错误信息。"""
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('refused')

    api = ApiClient(httpx.Client(transport=httpx.MockTransport(handler),
                                 base_url='http://user:pa55@h:1/'))
    with pytest.raises(CliError) as ei:
        api.get('/api/health')
    assert ei.value.code == EXIT_UNREACHABLE
    assert 'pa55' not in ei.value.message and '@' not in ei.value.message


def test_api_client_invalid_url_at_request_time(monkeypatch):
    """请求期才抛出的 InvalidURL(ApiClient 层)同样转成 CliError。"""
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL("Invalid port: '57l8'")

    transport = httpx.MockTransport(handler)
    api = ApiClient(httpx.Client(transport=transport, base_url='http://stub'))
    with pytest.raises(CliError) as ei:
        api.get('/api/health')
    assert '无效的服务地址' in ei.value.message


def test_resolve_exact_name_beats_id_prefix(cli, sqlite_db):
    """名称恰好是另一连接 id 前缀时,必须解析到同名连接(精确名称 > id 前缀)。"""
    import json
    other = _add_sqlite(cli, sqlite_db)
    items = json.loads(cli.invoke(app, ['conn', 'list', '--format', 'json']).output)
    aid = next(c['id'] for c in items if c['name'] == other)
    tricky = aid[:8]                            # 同时是 other 的唯一 id 前缀
    r = cli.invoke(app, ['conn', 'add', '--name', tricky, '--type', 'sqlite',
                         '--param', f'path={sqlite_db}'])
    assert r.exit_code == 0, r.output
    r = cli.invoke(app, ['conn', 'update', tricky, '--name', f'{tricky}-改名'])
    assert r.exit_code == 0, r.output
    assert f'已更新 {tricky} ' in r.output       # 改的是同名连接,不是 id 前缀命中的 other
    after = json.loads(cli.invoke(app, ['conn', 'list', '--format', 'json']).output)
    renamed = next(c for c in after if c['name'] == f'{tricky}-改名')
    assert renamed['id'] != aid
    assert any(c['id'] == aid and c['name'] == other for c in after)   # other 未被误改


def test_resolve_by_name_prefix(cli, sqlite_db):
    """唯一名称前缀可用:id 前缀没命中时退化到名称前缀。"""
    import uuid
    base = f'npfx-{uuid.uuid4().hex[:6]}'
    _add_sqlite(cli, sqlite_db, name=f'{base}-a', exact=True)
    r = cli.invoke(app, ['conn', 'test', base])
    assert r.exit_code == 0, r.output
    assert '连接成功' in r.output


def test_resolve_name_prefix_ambiguous_lists_candidates(cli, sqlite_db):
    """名称前缀命中多个:报错并列出候选,不能静默挑一个。"""
    import uuid
    base = f'amb-{uuid.uuid4().hex[:6]}'
    _add_sqlite(cli, sqlite_db, name=f'{base}-1', exact=True)
    _add_sqlite(cli, sqlite_db, name=f'{base}-2', exact=True)
    r = cli.invoke(app, ['conn', 'test', base])
    assert r.exit_code == 1
    assert '匹配到多个' in r.output
    assert f'{base}-1' in r.output and f'{base}-2' in r.output


def test_tree_and_ddl_escape_control_chars(monkeypatch):
    """tree/ddl 的库名、DDL 文本里的 ESC 序列要转义(与 conn list/history 同口径)。"""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == '/api/connections':
            return httpx.Response(200, json={'items': [_STUB_CONN]})
        if req.url.path.endswith('/metadata'):
            return httpx.Response(200, json={'items': [
                {'label': '\x1b[31mtbl', 'path': 'tbl', 'has_children': False}]})
        return httpx.Response(200, json={'ddl': 'CREATE TABLE "t"\n(\x1b]0;x\x07 v INT)'})

    cli = _stub_cli(monkeypatch, handler)
    r = cli.invoke(app, ['tree', 'stub-conn'])
    assert r.exit_code == 0, r.output
    assert '\x1b' not in r.output and '\x07' not in r.output
    assert '\\x1b[31mtbl' in r.output
    r = cli.invoke(app, ['ddl', 'stub-conn', 't'])
    assert r.exit_code == 0, r.output
    assert '\x1b' not in r.output and '\x07' not in r.output
    assert '\\x1b]0;x\\x07' in r.output and '\n' in r.output   # 多行结构不被破坏


def test_query_stdin_gbk(cli, sqlite_db):
    """--stdin 传 GBK 字节:utf-8 严格解码失败后回退 gbk,不能留下替换字符。"""
    name = _add_readonly_sqlite(cli, sqlite_db)
    r = cli.invoke(app, ['query', name, '--stdin', '--format', 'raw'],
                   input="select name from users where city='上海'".encode('gbk'))
    assert r.exit_code == 0, r.output
    assert '张三' in r.output and '�' not in r.output


def test_query_stdin_invalid_encoding(cli, sqlite_db):
    """GBK 也解不开的字节:中文报错 + 退出码 1,不是裸 traceback。"""
    name = _add_readonly_sqlite(cli, sqlite_db)
    r = cli.invoke(app, ['query', name, '--stdin'], input=b'\xff\xfe\x00\x01\x80abc')
    assert r.exit_code == 1
    assert '编码' in r.output and 'Traceback' not in r.output


def test_query_out_affected_only_writes_empty_file(cli, sqlite_conn_id, tmp_path):
    """-o 但语句没有结果集(纯 insert):写出空文件 + stderr 提示 + 退出码 0。

    必须落一个 0 字节文件:否则 `dbs query … -o out.csv && cat out.csv` 会读到上一次的陈旧内容。
    """
    out = tmp_path / 'none.csv'
    out.write_text('stale,data\n', encoding='utf-8')     # 预置陈旧文件,验证会被截断
    r = cli.invoke(app, ['query', sqlite_conn_id,
                         "insert into users(name) values ('out-aff')", '-o', str(out)])
    assert r.exit_code == 0, r.output
    assert '无结果集' in r.output and '空文件' in r.output and '影响行数: 1' in r.output
    assert out.exists() and out.read_bytes() == b''      # 陈旧内容已被截断
    # 出错时仍然按业务失败退出(不能把真失败吞成 0)
    r = cli.invoke(app, ['query', sqlite_conn_id, 'select * from nope', '-o', str(out)])
    assert r.exit_code == 1
    assert 'nope' in r.output


def test_history_limit_local_validation(cli):
    """history 的 limit 超过服务端 500:本地报错并点明服务端上限。"""
    r = cli.invoke(app, ['history', '--limit', '600'])
    assert r.exit_code == 1
    assert '1~500' in r.output and '服务端上限 500' in r.output
    r = cli.invoke(app, ['history', '--limit', '0'])
    assert r.exit_code == 1


def test_ai_ask_ctrl_c_cleans_session(cli, sqlite_conn_id, monkeypatch):
    """Ctrl+C(KeyboardInterrupt)中断:自动新建的孤儿会话同样要删掉。"""
    import json

    import backend.cli.commands.ai as ai_cmd

    def interrupt(url, payload, timeout):
        raise KeyboardInterrupt
        yield   # 生成器占位:异常在首次迭代时抛出

    monkeypatch.setattr(ai_cmd, 'stream_ai_events', interrupt)
    r = cli.invoke(app, ['ai', 'ask', sqlite_conn_id, '被中断的会话问题'])
    assert r.exit_code != 0   # click 把 KeyboardInterrupt 归一成 130(SIGINT 约定),不锁死具体值
    sessions = json.loads(cli.invoke(app, ['ai', 'sessions', 'list', '--format', 'json']).output)
    assert not any(s['title'].startswith('被中断的会话问题') for s in sessions)


def test_serve_defaults_to_localhost(monkeypatch):
    """serve 默认只监听本机(--host / DBSHOOTER_HOST 优先)。"""
    import uvicorn
    captured: dict = {}
    monkeypatch.setattr(uvicorn, 'run', lambda *a, **k: captured.update(k))
    monkeypatch.delenv('DBSHOOTER_HOST', raising=False)
    r = CliRunner().invoke(app, ['serve'])
    assert r.exit_code == 0, r.output
    assert captured['host'] == '127.0.0.1'
    monkeypatch.setenv('DBSHOOTER_HOST', '0.0.0.0')
    r = CliRunner().invoke(app, ['serve'])
    assert r.exit_code == 0, r.output
    assert captured['host'] == '0.0.0.0'


def test_serve_exposed_without_token_warns(monkeypatch):
    """监听非本机地址且没设令牌:启动信息要提示风险与设置方法。"""
    import uvicorn
    captured: dict = {}
    monkeypatch.setattr(uvicorn, 'run', lambda *a, **k: captured.update(k))
    monkeypatch.setenv('DBSHOOTER_HOST', '0.0.0.0')
    monkeypatch.delenv('DBSHOOTER_TOKEN', raising=False)
    r = CliRunner().invoke(app, ['serve'])
    assert r.exit_code == 0, r.output
    assert 'DBSHOOTER_TOKEN' in r.output and '警告' in r.output
    # 已设令牌时只提示已保护,不再吓人
    monkeypatch.setenv('DBSHOOTER_TOKEN', 'tk')
    r = CliRunner().invoke(app, ['serve'])
    assert r.exit_code == 0, r.output
    assert '已启用令牌校验' in r.output
