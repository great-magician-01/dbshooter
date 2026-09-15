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
