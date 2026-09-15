"""存储层:连接 / Provider / 会话 / 页签 / 历史 / settings。"""
import sqlite3

from backend.app import db


# ── 连接 ──

def test_connection_crud_and_encryption(sqlite_db):
    item = db.create_connection({'name': 'a', 'type': 'sqlite',
                                 'params': {'path': sqlite_db}, 'password': 'pw123'})
    assert item['id'] and item['has_password'] is True
    assert 'password' not in item and 'password_enc' not in item

    # 明文绝不出现在库文件里
    raw = sqlite3.connect(db.config.DB_PATH)
    stored = raw.execute('SELECT password_enc FROM connections WHERE id=?', (item['id'],)).fetchone()[0]
    assert stored and 'pw123' not in stored

    # 内部读取可解密
    full = db.get_connection(item['id'])
    assert full['password'] == 'pw123'

    # 更新时密码留空 = 保持原值
    db.update_connection({'id': item['id'], 'name': 'a2', 'type': 'sqlite',
                          'params': {'path': sqlite_db}})
    assert db.get_connection(item['id'])['password'] == 'pw123'
    by_id = {r['id']: r for r in db.list_connections()}
    assert by_id[item['id']]['name'] == 'a2'

    db.delete_connection(item['id'])
    assert db.get_connection(item['id']) is None


def test_connection_list_masks_secret(sqlite_db):
    db.create_connection({'name': 'm', 'type': 'mysql', 'host': 'h', 'password': 'topsecret'})
    for row in db.list_connections():
        assert 'password' not in row and 'password_enc' not in row


# ── AI Provider:多配置单生效 ──

def test_provider_single_active():
    a = db.create_provider({'name': 'A', 'base_url': 'http://a/v1', 'model': 'm1'})
    b = db.create_provider({'name': 'B', 'base_url': 'http://b/v1', 'model': 'm2',
                            'api_key': 'k'})
    db.set_active_provider(a['id'])
    db.set_active_provider(b['id'])
    rows = {r['id']: r['is_active'] for r in db.list_providers() if r['id'] in (a['id'], b['id'])}
    assert rows == {a['id']: False, b['id']: True}
    assert db.get_active_provider()['name'] == 'B'
    assert db.get_active_provider()['api_key'] == 'k'  # 内部可读明文
    db.delete_provider(a['id'])
    db.delete_provider(b['id'])


# ── AI 会话与消息 ──

def test_sessions_messages_cascade():
    s = db.create_session({'title': 't', 'connection_id': None})
    db.add_message(s['id'], 'user', '查一下订单')
    db.add_message(s['id'], 'assistant', '{"text":"...","sql":"SELECT 1"}')
    msgs = db.list_messages(s['id'])
    assert [m['role'] for m in msgs] == ['user', 'assistant']

    db.rename_session(s['id'], '新标题')
    assert db.list_sessions()[0]['title'] == '新标题'

    db.delete_session(s['id'])
    assert db.list_messages(s['id']) == []


# ── 工作区页签 ──

def test_tabs_upsert_order_active():
    db.save_tab({'id': 't1', 'type': 'sql', 'title': 'SQL-1', 'content': 'SELECT 1', 'sort': 0})
    db.save_tab({'id': 't2', 'type': 'redis', 'title': '键浏览', 'sort': 1})
    # upsert:同 id 更新内容
    db.save_tab({'id': 't1', 'type': 'sql', 'title': 'SQL-1', 'content': 'SELECT 2', 'sort': 0})
    tabs = {t['id']: t for t in db.list_tabs()}
    assert tabs['t1']['content'] == 'SELECT 2'

    db.save_tab_order(['t2', 't1'], 't2')
    tabs = db.list_tabs()
    assert [t['id'] for t in tabs] == ['t2', 't1']
    assert tabs[0]['is_active'] is True and tabs[1]['is_active'] is False

    db.delete_tab('t1')
    assert {t['id'] for t in db.list_tabs()} == {'t2'}


# ── 历史与设置 ──

def test_history_and_settings():
    db.add_history('c-unit-test', 'SELECT 1', 12, 3, 'done')
    items = [h for h in db.list_history() if h['connection_id'] == 'c-unit-test']
    assert items[0]['stmt'] == 'SELECT 1'

    db.save_settings({'theme': 'dark', 'page_size': 500})
    db.save_settings({'theme': 'light'})
    s = db.get_settings()
    assert s['theme'] == 'light' and s['page_size'] == '500'
