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


def test_query_execute_with_schema(client, sqlite_conn_id):
    # schema 绑定(右键 PG schema 新建的标签页)仅 PG 生效,其余驱动忽略
    r = client.post('/api/query/execute',
                    json={'conn_id': sqlite_conn_id, 'stmt': 'SELECT * FROM users',
                          'schema': 'main'})
    assert r.status_code == 200
    assert len(r.json()['results'][0]['rows']) == 3


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


def test_rest_limit_capped(client, sqlite_conn_id):
    """REST 通道 limit 必须设上限,防大 limit 把内存打爆(WS 主通道固定 BUFFER_CAP)。"""
    r = client.post('/api/query/execute',
                    json={'conn_id': sqlite_conn_id, 'stmt': 'SELECT 1', 'limit': 9999999})
    assert r.status_code == 422
    r = client.post('/api/query/export',
                    json={'conn_id': sqlite_conn_id, 'stmt': 'SELECT 1', 'limit': 9999999})
    assert r.status_code == 422
    # 边界内的值照常
    r = client.post('/api/query/execute',
                    json={'conn_id': sqlite_conn_id, 'stmt': 'SELECT 1', 'limit': 5000})
    assert r.status_code == 200


def test_update_connection_merge_semantics(client, sqlite_db):
    """update 缺省字段沿用旧值:漏传 readonly/params 不应静默解除只读/清空参数。"""
    cid = client.post('/api/connections', json={
        'name': 'ro', 'type': 'sqlite', 'params': {'path': sqlite_db},
        'readonly': True, 'database': 'x'}).json()['item']['id']
    # 只改名字,不传 params/readonly/database
    r = client.post('/api/connections/update',
                    json={'id': cid, 'name': '改名', 'type': 'sqlite'})
    item = r.json()['item']
    assert item['readonly'] is True
    assert item['params'] == {'path': sqlite_db}
    assert item['database'] == 'x'
    # 只读仍生效(连接还能用,说明 params 未丢)
    r = client.post('/api/query/execute', json={'conn_id': cid, 'stmt': 'DELETE FROM users'})
    assert r.status_code == 400 and '只读' in r.json()['detail']
    # 显式传 false 才解除
    client.post('/api/connections/update',
                json={'id': cid, 'name': '改名', 'type': 'sqlite', 'readonly': False})
    items = {i['id']: i for i in client.get('/api/connections').json()['items']}
    assert items[cid]['readonly'] is False


def test_settings_key_validation(client):
    assert client.post('/api/settings/save',
                        json={'values': {'theme': 'dark'}}).status_code == 200
    assert client.post('/api/settings/save',
                       json={'values': {'bad key!': 'x'}}).status_code == 422
    assert client.post('/api/settings/save',
                       json={'values': {'x' * 65: 'x'}}).status_code == 422


def test_export_csv_formula_injection(client, sqlite_conn_id):
    """Excel 公式注入防护:=/+/-/@ 开头的单元格前缀单引号。"""
    client.post('/api/query/execute', json={
        'conn_id': sqlite_conn_id,
        'stmt': "CREATE TABLE f(v TEXT); INSERT INTO f VALUES ('=SUM(A1:A2)'), ('普通')"})
    r = client.post('/api/query/export', json={'conn_id': sqlite_conn_id,
                                               'stmt': 'SELECT v FROM f'})
    text = r.content.decode('utf-8')
    assert "'=SUM(A1:A2)" in text and '普通' in text


def test_ddl_repeated_table_params(client, sqlite_conn_id):
    """ddl 路由支持重复 tables 参数与逗号分隔两种传法。"""
    r = client.get(f'/api/connections/{sqlite_conn_id}/ddl',
                   params=[('tables', 'users'), ('tables', 'v_users')])
    assert r.status_code == 200 and 'CREATE TABLE' in r.json()['ddl']
    r = client.get(f'/api/connections/{sqlite_conn_id}/ddl?tables=users,v_users')
    assert 'CREATE VIEW' in r.json()['ddl']


def test_structure_endpoint(client, sqlite_conn_id):
    r = client.get(f'/api/connections/{sqlite_conn_id}/structure',
                   params={'path': 'main.users'})
    assert r.status_code == 200
    cols = r.json()['columns']
    assert [c['name'] for c in cols] == ['id', 'name', 'city']
    assert cols[0]['pk'] == 1 and cols[0]['type'] == 'INTEGER'
    # 视图也有结构
    r = client.get(f'/api/connections/{sqlite_conn_id}/structure',
                   params={'path': 'main.v_users'})
    assert [c['name'] for c in r.json()['columns']] == ['name']


def test_structure_missing_table_404(client, sqlite_conn_id):
    """表不存在 → 404(而不是 200 + 空列,前端会把空列当成"没有列")。"""
    r = client.get(f'/api/connections/{sqlite_conn_id}/structure',
                   params={'path': 'main.no_such'})
    assert r.status_code == 404


def test_structure_relations_empty_path_400(client, sqlite_conn_id):
    """缺 path 是调用方 bug,与"连接不存在"区分开。"""
    for url in ('structure', 'relations'):
        r = client.get(f'/api/connections/{sqlite_conn_id}/{url}')
        assert r.status_code == 400, url


def test_relations_endpoint(client, sqlite_conn_id):
    r = client.get(f'/api/connections/{sqlite_conn_id}/relations',
                   params={'path': 'main.orders'})
    assert r.status_code == 200
    rels = r.json()['relations']
    assert len(rels) == 1 and rels[0]['direction'] == 'out'
    assert rels[0]['ref_table'] == 'users' and rels[0]['ref_column'] == 'id'
    # 入站
    r = client.get(f'/api/connections/{sqlite_conn_id}/relations',
                   params={'path': 'main.users'})
    rels = r.json()['relations']
    assert len(rels) == 1 and rels[0]['direction'] == 'in' and rels[0]['table'] == 'orders'
    # 视图无关系
    r = client.get(f'/api/connections/{sqlite_conn_id}/relations',
                   params={'path': 'main.v_users'})
    assert r.json()['relations'] == []


def test_structure_relations_missing_connection_404(client):
    r = client.get('/api/connections/nope/structure', params={'path': 'main.users'})
    assert r.status_code == 404
    r = client.get('/api/connections/nope/relations', params={'path': 'main.users'})
    assert r.status_code == 404
