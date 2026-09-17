"""WebSocket 通道:query.execute 事件流 + ai.text2sql(mock 掉 LLM 调用)。"""
import json

from backend.app.services import ai_service


def _collect(ws, stop_events):
    events = []
    while True:
        msg = ws.receive_json()
        events.append(msg)
        if msg.get('event') in stop_events:
            return events


def test_ws_query_execute(client, sqlite_conn_id):
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'id': 'r1', 'type': 'query.execute',
                      'payload': {'conn_id': sqlite_conn_id, 'stmt': 'SELECT * FROM users'}})
        events = _collect(ws, {'query.done', 'query.error'})
    names = [e['event'] for e in events]
    assert names[0] == 'query.started'
    assert 'query.rows' in names and names[-1] == 'query.done'
    rows_ev = next(e for e in events if e['event'] == 'query.rows')
    assert len(rows_ev['data']['rows']) == 3
    assert rows_ev['data']['columns'][0]['name'] == 'id'

    # 分页接口读同一份缓冲
    qid = events[0]['data']['query_id']
    page = client.get(f'/api/query/{qid}/rows?offset=1&limit=1').json()
    assert page['rows'][0][1] == '李四'


def test_ws_query_execute_with_schema(client, sqlite_conn_id):
    # schema 由 SQL 页签上下文传入(SQLite 忽略该字段,PG 用于 SET LOCAL search_path)
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'id': 'r3', 'type': 'query.execute',
                      'payload': {'conn_id': sqlite_conn_id, 'stmt': 'SELECT * FROM users',
                                  'schema': 'main'}})
        events = _collect(ws, {'query.done', 'query.error'})
    assert events[-1]['event'] == 'query.done'


def test_ws_query_non_json_types(client, sqlite_conn_id):
    # BLOB 等驱动原生类型(bytes/datetime/Decimal)经 WS 通道原生 json.dumps 推送,
    # 必须在驱动出口 JSON 化,否则报 "Object of type bytes is not JSON serializable"
    r = client.post('/api/query/execute', json={
        'conn_id': sqlite_conn_id,
        'stmt': "CREATE TABLE blobs(id INTEGER PRIMARY KEY, data BLOB);"
                " INSERT INTO blobs(data) VALUES (x'00FF'), (CAST('文本' AS BLOB))"})
    assert r.status_code == 200, r.text
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'id': 'r4', 'type': 'query.execute',
                      'payload': {'conn_id': sqlite_conn_id, 'stmt': 'SELECT * FROM blobs'}})
        events = _collect(ws, {'query.done', 'query.error'})
    assert events[-1]['event'] == 'query.done'
    rows_ev = next(e for e in events if e['event'] == 'query.rows')
    assert rows_ev['data']['rows'][0][1] == '00ff'    # 二进制 → 十六进制
    assert rows_ev['data']['rows'][1][1] == '文本'     # 文本 BLOB → 还原


def test_ws_query_error_event(client, sqlite_conn_id):
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'id': 'r2', 'type': 'query.execute',
                      'payload': {'conn_id': sqlite_conn_id, 'stmt': 'SELECT * FROM nope'}})
        events = _collect(ws, {'query.done', 'query.error'})
    assert events[-1]['event'] == 'query.error'
    assert events[-1]['data']['error']


def test_ws_unknown_type(client):
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'id': 'x', 'type': 'nope', 'payload': {}})
        msg = ws.receive_json()
    assert msg['event'] == 'error'


def test_ws_malformed_query_payload(client):
    """缺 conn_id/stmt 必须回错误事件,而不是让请求悬死。"""
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'id': 'q1', 'type': 'query.execute', 'payload': {}})
        events = _collect(ws, {'query.done', 'query.error'})
    assert events[-1]['event'] == 'query.error'
    assert 'conn_id' in events[-1]['data']['error']


def test_ws_non_dict_payload(client):
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'id': 'q2', 'type': 'query.execute', 'payload': 'not-a-dict'})
        msg = ws.receive_json()
    assert msg['event'] == 'error' and 'payload' in msg['data']['message']


def test_ws_ai_bogus_session(client):
    """不存在的 session_id 应得到可读错误,而不是 FK 违例异常悬死。"""
    p = client.post('/api/ai/providers', json={
        'name': 'mock', 'base_url': 'http://mock/v1', 'model': 'm'}).json()['item']
    client.post('/api/ai/providers/activate', json={'id': p['id']})
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'id': 'a9', 'type': 'ai.text2sql',
                      'payload': {'session_id': 'no-such-session', 'question': 'hi'}})
        events = _collect(ws, {'ai.done', 'ai.error'})
    assert events[-1]['event'] == 'ai.error'
    assert '会话不存在' in events[-1]['data']['message']


def test_ws_ai_text2sql(client, monkeypatch):
    # 准备 provider + 会话
    p = client.post('/api/ai/providers', json={
        'name': 'mock', 'base_url': 'http://mock/v1', 'model': 'm'}).json()['item']
    client.post('/api/ai/providers/activate', json={'id': p['id']})
    sid = client.post('/api/ai/sessions', json={'title': 't'}).json()['item']['id']

    async def fake_stream(provider, messages):
        yield '好的,查询如下:\n```sql\nSELECT * FROM users LIMIT 200;\n```'

    monkeypatch.setattr(ai_service, 'stream_chat', fake_stream)

    with client.websocket_connect('/ws') as ws:
        ws.send_json({'id': 'a1', 'type': 'ai.text2sql',
                      'payload': {'session_id': sid, 'conn_id': None,
                                  'question': '查所有用户', 'tables': []}})
        events = _collect(ws, {'ai.done', 'ai.error'})
    names = [e['event'] for e in events]
    assert names[0] == 'ai.started' and 'ai.token' in names and names[-1] == 'ai.done'
    done = events[-1]['data']
    assert done['sql'] == 'SELECT * FROM users LIMIT 200;'

    # 会话消息已落库(user + assistant)
    msgs = client.get(f'/api/ai/sessions/{sid}/messages').json()['items']
    assert [m['role'] for m in msgs] == ['user', 'assistant']
    assert json.loads(msgs[1]['content'])['sql'] == done['sql']


def test_ws_ai_without_provider(client, sqlite_conn_id):
    # 清空 provider(其他测试可能建过)
    from backend.app import db
    for p in db.list_providers():
        db.delete_provider(p['id'])
    sid = client.post('/api/ai/sessions', json={'title': 't'}).json()['item']['id']
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'id': 'a2', 'type': 'ai.text2sql',
                      'payload': {'session_id': sid, 'question': 'hi'}})
        events = _collect(ws, {'ai.done', 'ai.error'})
    assert events[-1]['event'] == 'ai.error'
    assert 'Provider' in events[-1]['data']['message']


def _mk_provider(client) -> str:
    p = client.post('/api/ai/providers', json={
        'name': 'mock', 'base_url': 'http://mock/v1', 'model': 'm'}).json()['item']
    client.post('/api/ai/providers/activate', json={'id': p['id']})
    return client.post('/api/ai/sessions', json={'title': 't'}).json()['item']['id']


def test_ws_ai_text2sql_with_tools(client, sqlite_conn_id, monkeypatch):
    """带 conn_id 时走工具循环:模型先 describe_table(真实 sqlite 驱动),再出 SQL。"""
    sid = _mk_provider(client)
    rounds = [
        ('', [ai_service.ToolCallReq('c1', 'describe_table', '{"table": "users"}')]),
        ('查好了:\n```sql\nSELECT count(*) FROM users;\n```', []),
    ]

    async def fake_round(provider, messages, tools=None, on_token=None):
        assert tools, '工具模式下每轮都应携带 tools'
        text, calls = rounds.pop(0)
        if on_token and text:
            await on_token(text)
        return text, calls

    monkeypatch.setattr(ai_service, 'stream_round', fake_round)

    with client.websocket_connect('/ws') as ws:
        ws.send_json({'id': 'a3', 'type': 'ai.text2sql',
                      'payload': {'session_id': sid, 'conn_id': sqlite_conn_id,
                                  'question': '查用户数'}})
        events = _collect(ws, {'ai.done', 'ai.error'})
    names = [e['event'] for e in events]
    assert names[0] == 'ai.started' and names[-1] == 'ai.done'
    tool_evs = [e['data'] for e in events if e['event'] == 'ai.tool']
    assert [t['status'] for t in tool_evs] == ['running', 'done']
    assert tool_evs[1]['summary'] == '查看 users 表结构'
    done = events[-1]['data']
    assert done['sql'] == 'SELECT count(*) FROM users;'
    assert done['tools'][0]['name'] == 'describe_table'

    # 落库的 assistant 消息带工具轨迹
    msgs = client.get(f'/api/ai/sessions/{sid}/messages').json()['items']
    saved = json.loads(msgs[1]['content'])
    assert saved['tools'][0]['call_id'] == 'c1'
    assert 'CREATE TABLE users' not in saved['text']   # 工具结果不进正文


def test_ws_ai_tool_error_event(client, sqlite_conn_id, monkeypatch):
    """模型描述了不存在的表:ai.tool 终态 error,随后仍正常 ai.done。"""
    sid = _mk_provider(client)
    rounds = [
        ('', [ai_service.ToolCallReq('c1', 'describe_table', '{"table": "nope"}')]),
        ('该表不存在,请确认表名。', []),
    ]

    async def fake_round(provider, messages, tools=None, on_token=None):
        text, calls = rounds.pop(0)
        if on_token and text:
            await on_token(text)
        return text, calls

    monkeypatch.setattr(ai_service, 'stream_round', fake_round)

    with client.websocket_connect('/ws') as ws:
        ws.send_json({'id': 'a4', 'type': 'ai.text2sql',
                      'payload': {'session_id': sid, 'conn_id': sqlite_conn_id,
                                  'question': '查 nope 表'}})
        events = _collect(ws, {'ai.done', 'ai.error'})
    tool_evs = [e['data'] for e in events if e['event'] == 'ai.tool']
    assert [t['status'] for t in tool_evs] == ['running', 'error']
    assert events[-1]['event'] == 'ai.done'
