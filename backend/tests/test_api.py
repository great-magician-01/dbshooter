"""REST API 端到端(TestClient,SQLite 真实库,外部库不连真实服务)。"""


def test_health(client):
    r = client.get('/api/health')
    assert r.status_code == 200
    assert set(r.json()['drivers']) == {'mongo', 'mysql', 'pg', 'redis', 'sqlite'}


def test_connection_lifecycle(client, sqlite_db):
    # 创建
    r = client.post('/api/connections', json={
        'name': 'demo', 'type': 'sqlite', 'params': {'path': sqlite_db}, 'password': 'x'})
    assert r.status_code == 200
    item = r.json()['item']
    assert item['has_password'] and 'password' not in item

    # 列表
    items = client.get('/api/connections').json()['items']
    assert any(i['id'] == item['id'] for i in items)

    # 测试连接(按 id)
    r = client.post('/api/connections/test', json={'id': item['id']})
    assert r.json()['ok'] and 'SQLite' in r.json()['message']

    # 测试连接(临时配置)
    r = client.post('/api/connections/test', json={'config': {
        'name': 'tmp', 'type': 'sqlite', 'params': {'path': sqlite_db}}})
    assert r.json()['ok']

    # 更新
    r = client.post('/api/connections/update',
                    json={'id': item['id'], 'name': 'demo2', 'type': 'sqlite',
                          'params': {'path': sqlite_db}})
    assert r.json()['item']['name'] == 'demo2'

    # 元数据
    nodes = client.get(f"/api/connections/{item['id']}/metadata").json()['items']
    assert nodes[0]['label'] == 'main'
    tables = client.get(f"/api/connections/{item['id']}/metadata?path=main").json()['items']
    assert any(t['label'] == 'users' for t in tables)

    # DDL
    r = client.get(f"/api/connections/{item['id']}/ddl?tables=users")
    assert 'CREATE TABLE' in r.json()['ddl']

    # 删除
    r = client.post('/api/connections/delete', json={'id': item['id']})
    assert r.json()['ok']
    assert client.post('/api/connections/test', json={'id': item['id']}).status_code == 404


def test_query_execute_and_history(client, sqlite_conn_id):
    r = client.post('/api/query/execute',
                    json={'conn_id': sqlite_conn_id, 'stmt': 'SELECT * FROM users'})
    res = r.json()['results'][0]
    assert res['kind'] == 'rows' and len(res['rows']) == 3

    r = client.post('/api/query/execute',
                    json={'conn_id': sqlite_conn_id, 'stmt': 'SELECT * FROM nope'})
    assert r.json()['results'][0]['error']

    hist = client.get('/api/query/history').json()['items']
    assert len(hist) >= 2 and hist[0]['stmt']


def test_query_export_csv(client, sqlite_conn_id):
    r = client.post('/api/query/export',
                    json={'conn_id': sqlite_conn_id, 'stmt': 'SELECT id, name FROM users'})
    assert r.status_code == 200
    text = r.content.decode('utf-8-sig')
    assert 'id,name' in text.replace('\r', '') and '张三' in text


def test_workspace_flow(client):
    r = client.post('/api/workspace/tabs/save',
                    json={'id': 't1', 'type': 'sql', 'title': 'SQL-1',
                          'content': 'SELECT 1', 'sort': 0})
    assert r.status_code == 200
    client.post('/api/workspace/tabs/save',
                json={'id': 't2', 'type': 'sql', 'title': 'SQL-2', 'content': 'SELECT 2'})
    # 防抖保存 = 同 id upsert
    client.post('/api/workspace/tabs/save',
                json={'id': 't1', 'type': 'sql', 'title': 'SQL-1', 'content': 'SELECT 11'})
    client.post('/api/workspace/tabs/order', json={'ids': ['t2', 't1'], 'active_id': 't1'})
    tabs = client.get('/api/workspace/tabs').json()['items']
    assert [t['id'] for t in tabs] == ['t2', 't1']
    assert tabs[1]['is_active'] and tabs[1]['content'] == 'SELECT 11'

    client.post('/api/workspace/tabs/delete', json={'id': 't2'})
    assert {t['id'] for t in client.get('/api/workspace/tabs').json()['items']} == {'t1'}


def test_settings(client):
    client.post('/api/settings/save', json={'values': {'theme': 'light'}})
    assert client.get('/api/settings').json()['values']['theme'] == 'light'


def test_ai_provider_flow(client):
    r = client.post('/api/ai/providers', json={
        'name': 'DeepSeek', 'base_url': 'https://api.deepseek.com/v1',
        'api_key': 'sk-x', 'model': 'deepseek-chat'})
    p1 = r.json()['item']
    assert p1['has_api_key'] and 'api_key' not in p1

    r = client.post('/api/ai/providers', json={
        'name': 'Ollama', 'base_url': 'http://127.0.0.1:11434/v1', 'model': 'qwen2.5'})
    p2 = r.json()['item']

    client.post('/api/ai/providers/activate', json={'id': p1['id']})
    client.post('/api/ai/providers/activate', json={'id': p2['id']})
    items = client.get('/api/ai/providers').json()['items']
    actives = [i for i in items if i['is_active']]
    assert len(actives) == 1 and actives[0]['id'] == p2['id']

    # 更新时 api_key 留空 → 保持
    client.post('/api/ai/providers/update', json={
        'id': p1['id'], 'name': 'DeepSeek', 'base_url': 'https://api.deepseek.com/v1',
        'model': 'deepseek-v3'})
    items = {i['id']: i for i in client.get('/api/ai/providers').json()['items']}
    assert items[p1['id']]['model'] == 'deepseek-v3' and items[p1['id']]['has_api_key']

    client.post('/api/ai/providers/delete', json={'id': p2['id']})


def test_ai_sessions(client):
    r = client.post('/api/ai/sessions', json={'title': '订单统计'})
    sid = r.json()['item']['id']
    client.post('/api/ai/sessions/rename', json={'id': sid, 'title': '订单分析'})
    sessions = client.get('/api/ai/sessions').json()['items']
    assert sessions[0]['title'] == '订单分析'
    client.post('/api/ai/sessions/delete', json={'id': sid})
    assert all(s['id'] != sid for s in client.get('/api/ai/sessions').json()['items'])


def test_readonly_enforced_via_api(client, sqlite_db):
    r = client.post('/api/connections', json={
        'name': 'ro', 'type': 'sqlite', 'params': {'path': sqlite_db}, 'readonly': True})
    cid = r.json()['item']['id']
    r = client.post('/api/query/execute',
                    json={'conn_id': cid, 'stmt': 'DELETE FROM users'})
    assert r.status_code == 400 and '只读' in r.json()['detail']
    r = client.post('/api/query/execute',
                    json={'conn_id': cid, 'stmt': 'SELECT count(*) FROM users'})
    assert r.status_code == 200
