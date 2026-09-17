"""代码审查修复的回归测试:只读拦截、RETURNING 提交、令牌边界、params 加密脱敏等。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import pytest

from backend.app import db, security
from backend.app.drivers import QueryError, ReadonlyViolation, create_driver
from backend.app.drivers.base import ensure_writable, first_keyword
from backend.app.drivers.sqlutil import jsonable


# ── 只读拦截:首词提取(注释剥离) ──

@pytest.mark.parametrize('stmt', [
    '-- select\nDROP TABLE t',            # 注释首词污染绕过(旧实现把 '-' 当空白剥掉)
    '--- select\nDROP TABLE t',
    '- select\nDROP TABLE t',
    '/* select */ DROP TABLE t',
    '/*! select */ DROP TABLE t',         # MySQL 版本注释是可执行的,不可当注释剥
    '/*M! select */ DROP TABLE t',        # MariaDB 同理
    'INSERT INTO t VALUES (1)',
    'SET default_transaction_read_only = off',   # 解除 PG 会话只读的语句本身
])
def test_ensure_writable_blocks(stmt):
    with pytest.raises(ReadonlyViolation):
        ensure_writable(stmt, True)


@pytest.mark.parametrize('stmt', [
    '-- 查询用户\nSELECT * FROM users',   # 带中文注释的正常查询(旧实现误伤)
    '/* 注释 */ SELECT 1',
    '# 注释\nSELECT 1',                   # MySQL 行注释
    '(SELECT 1)',
    'WITH x AS (SELECT 1) SELECT * FROM x',
    '--',                                 # 纯注释:旧实现 IndexError 500
    '(',
])
def test_ensure_writable_allows(stmt):
    ensure_writable(stmt, True)           # 不抛即通过
    ensure_writable('DELETE FROM t', False)   # 非只读连接不拦截


def test_first_keyword_pure_comment():
    assert first_keyword('--') == ''
    assert first_keyword('/* x */') == ''
    assert first_keyword('  (  SELECT 1') == 'select'


async def test_readonly_comment_prefix_bypass_end_to_end(sqlite_db):
    """端到端:只读连接上 '-- select\nDELETE' 必须被拦截(不再判成 select 放行)。"""
    d = create_driver({'type': 'sqlite', 'name': 't',
                       'params': {'path': sqlite_db}, 'readonly': True})
    with pytest.raises(ReadonlyViolation):
        await d.execute('-- select\nDELETE FROM users')
    res = await d.execute('-- 查全部\nSELECT * FROM users')   # 注释 + 查询放行
    assert res[0].kind == 'rows' and len(res[0].rows) == 3
    await d.close()


# ── SQLite RETURNING 提交 ──

async def test_insert_returning_commits(sqlite_db):
    """INSERT ... RETURNING 有结果集(description 非空)但写入必须落库。"""
    d = create_driver({'type': 'sqlite', 'name': 't', 'params': {'path': sqlite_db}})
    res = await d.execute("INSERT INTO users(name, city) VALUES ('钱七','杭州') RETURNING id")
    assert res[0].kind == 'rows' and res[0].rows
    await d.close()   # 关闭后重开验证:旧实现不 commit,连接关闭即回滚
    with sqlite3.connect(sqlite_db) as c:
        names = [r[0] for r in c.execute("SELECT name FROM users WHERE city='杭州'")]
    assert names == ['钱七']


async def test_insert_returning_over_limit_commits(sqlite_db):
    """RETURNING 行数超过 limit(未抽干游标)也必须提交:
    游标存活期间 commit 会报 "cannot commit transaction - SQL statements in progress",
    被吞成 error 结果且写入丢失。"""
    d = create_driver({'type': 'sqlite', 'name': 't', 'params': {'path': sqlite_db}})
    values = ', '.join(f"('u{i}','批量')" for i in range(5))
    res = await d.execute(
        f'INSERT INTO users(name, city) VALUES {values} RETURNING id', limit=2)
    assert [r.kind for r in res] == ['rows']   # 不得冒出 error 结果
    assert res[0].truncated is True
    await d.close()
    with sqlite3.connect(sqlite_db) as c:
        n = c.execute("SELECT count(*) FROM users WHERE city='批量'").fetchone()[0]
    assert n == 5


# ── jsonable 非有限浮点 ──

def test_jsonable_non_finite_floats():
    assert jsonable(float('inf')) == 'inf'
    assert jsonable(float('-inf')) == '-inf'
    assert jsonable(float('nan')) == 'nan'
    assert jsonable(1.5) == 1.5 and jsonable(3) == 3
    assert jsonable([float('inf')]) == ['inf']


def test_infinity_query_no_illegal_json(client, sqlite_conn_id):
    """9e999 → inf:REST 不再 500(allow_nan=False 的 ValueError),WS 不再产非法帧。"""
    r = client.post('/api/query/execute',
                    json={'conn_id': sqlite_conn_id, 'stmt': 'SELECT 9e999'})
    assert r.status_code == 200, r.text
    assert r.json()['results'][0]['rows'] == [['inf']]


# ── 令牌比较:非 ASCII 不再 500 ──

def test_token_matches_non_ascii(monkeypatch):
    from backend.app import config
    monkeypatch.setattr(config, 'ACCESS_TOKEN', 'sekrit')
    assert security.token_matches('tökén') is False   # 旧实现 TypeError
    assert security.token_matches('sekrit') is True
    assert security.token_matches(None) is False


def test_api_non_ascii_token_not_500(client, monkeypatch):
    from backend.app import config
    monkeypatch.setattr(config, 'ACCESS_TOKEN', 'sekrit')
    # header 值用 bytes 才能携带非 ASCII(httpx 对 str 值强制 ascii 编码),
    # 模拟原始客户端发来的非 ASCII 字节
    r = client.get('/api/connections',
                   headers={b'authorization': 'Bearer tökén'.encode('latin-1')})
    assert r.status_code == 401   # 拒绝,但不是 500


# ── params 加密落库 + URI 脱敏回显 ──

def test_params_encrypted_and_redacted(client):
    r = client.post('/api/connections', json={
        'name': 'm', 'type': 'mongo',
        'params': {'uri': 'mongodb://admin:SECRETPWD@example:27017/db'}})
    assert r.status_code == 200, r.text
    item = r.json()['item']
    # 回显脱敏,列表同样
    assert item['params']['uri'] == 'mongodb://admin:***@example:27017/db'
    assert 'SECRETPWD' not in json.dumps(client.get('/api/connections').json())
    # 落库是密文(带前缀),且能完整解密供驱动使用
    raw = db.one('SELECT params_json FROM connections WHERE id=?', (item['id'],))
    assert raw is not None and raw['params_json'].startswith('fernet:')
    assert 'SECRETPWD' not in raw['params_json']
    full = db.get_connection(item['id'])
    assert full is not None and full['params']['uri'].startswith('mongodb://admin:SECRETPWD@')


@pytest.mark.parametrize('uri,expected', [
    ('redis://:SECRETPW@h:6379/0', 'redis://:***@h:6379/0'),   # 空用户名 + 密码(常见形态)
    ('mongodb://u:p@h/db', 'mongodb://u:***@h/db'),
    ('mongodb://h/db', 'mongodb://h/db'),                      # 无认证:不动
    ('mongodb://u@h/db', 'mongodb://u@h/db'),                  # 只有用户名:不动
    ('C:/data/x.db', 'C:/data/x.db'),                          # 本地路径不误伤
])
def test_redact_uri_variants(uri, expected):
    from backend.app.db import _redact_params
    assert _redact_params({'uri': uri})['uri'] == expected


def test_params_plaintext_legacy_readable(client):
    """历史明文 params_json 行(无前缀)兼容读取。"""
    r = client.post('/api/connections', json={'name': 's', 'type': 'sqlite'})
    cid = r.json()['item']['id']
    db.run('UPDATE connections SET params_json=? WHERE id=?', ('{"path": "/tmp/x.db"}', cid))
    full = db.get_connection(cid)
    assert full is not None and full['params']['path'] == '/tmp/x.db'


def test_update_keeps_redacted_uri(client):
    """编辑时 uri 含 ***(脱敏回显)或留空 = 不修改;其他 params 键正常覆盖。"""
    r = client.post('/api/connections', json={
        'name': 'm2', 'type': 'mongo',
        'params': {'uri': 'mongodb://u:p@h/db'}})
    cid = r.json()['item']['id']
    r2 = client.post('/api/connections/update', json={
        'id': cid, 'name': 'm2', 'type': 'mongo',
        'params': {'uri': 'mongodb://u:***@h/db', 'extra': '1'}})
    assert r2.status_code == 200, r2.text
    full = db.get_connection(cid)
    assert full is not None
    assert full['params']['uri'] == 'mongodb://u:p@h/db'   # 未被脱敏值覆盖
    assert full['params']['extra'] == '1'


# ── /api/connections/test:id+config 合并库存密码 ──

def test_test_connection_merges_stored_password(client, monkeypatch):
    from backend.app.services.connection_manager import manager
    captured: dict = {}

    async def fake_test_config(cfg):
        captured.update(cfg)
        return True, 'ok'

    monkeypatch.setattr(manager, 'test_config', fake_test_config)
    r = client.post('/api/connections', json={
        'name': 'my', 'type': 'mysql', 'host': 'h1', 'password': 's3cret'})
    cid = r.json()['item']['id']
    # 编辑表单:改了 host,密码框留空 —— 旧行为拿空密码测连,假失败
    r2 = client.post('/api/connections/test', json={
        'id': cid, 'config': {'name': 'my', 'type': 'mysql', 'host': 'h2'}})
    assert r2.json() == {'ok': True, 'message': 'ok'}
    assert captured['password'] == 's3cret'   # 回填库存密码
    assert captured['host'] == 'h2'           # 表单改动生效


# ── 其他 API 健壮性 ──

def test_duplicate_connection_id_400(client):
    body = {'id': 'dup1', 'name': 'a', 'type': 'sqlite'}
    assert client.post('/api/connections', json=body).status_code == 200
    r = client.post('/api/connections', json=body)
    assert r.status_code == 400 and '已存在' in r.json()['detail']


def test_history_negative_limit_clamped(client):
    """limit=-1 在 SQLite 里等于"无上限"(LIMIT -1),必须被卡到下限 1。
    走真实 REST 路由断言(在 db 层抄路由表达式是自证式假阳性)。"""
    db.add_history('c-hist', 'SELECT 1', 1, 1, 'done')
    db.add_history('c-hist', 'SELECT 2', 1, 1, 'done')
    items = client.get('/api/query/history?limit=-1').json()['items']
    assert len(items) == 1


def test_save_tab_concurrent_upsert():
    """防抖重复提交同 id:UPSERT 幂等(顺序双写验证语义;真正的竞态由
    ON CONFLICT 从机制上消除,不再靠"先查后插")。"""
    t = {'id': 'tab-upsert-test', 'type': 'sql', 'title': 't', 'content': 'SELECT 1'}
    try:
        db.save_tab(t)
        row = db.save_tab({**t, 'content': 'SELECT 2'})
        assert row['content'] == 'SELECT 2'
    finally:
        db.delete_tab('tab-upsert-test')   # 共享库:清掉,避免影响 list_tabs 断言的其他用例


def test_ddl_missing_connection_404(client):
    r = client.get('/api/connections/no-such-conn/ddl?tables=t')
    assert r.status_code == 404


# ── WS:断开取消在途查询 + Origin 校验 ──

def test_ws_origin_check():
    from starlette.datastructures import Headers

    from backend.app.api.ws import _origin_allowed

    assert _origin_allowed(Headers({}))                     # 非浏览器(CLI)无 Origin
    assert _origin_allowed(Headers({'origin': 'http://127.0.0.1:5718',
                                    'host': '127.0.0.1:5718'}))
    assert not _origin_allowed(Headers({'origin': 'https://evil.com',
                                        'host': '127.0.0.1:5718'}))   # 跨站网页
    assert not _origin_allowed(Headers({'origin': 'not a url', 'host': 'h'}))


def test_ws_disconnect_cancels_running_query(client, sqlite_conn_id):
    """WS 断开 → 在途慢查询被取消(不再继续占用数据库)。"""
    from backend.app.services.query_service import query_service
    stmt = ('WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c)'
            ' SELECT count(*) FROM c')
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'id': 'r1', 'type': 'query.execute',
                      'payload': {'conn_id': sqlite_conn_id, 'stmt': stmt}})
        first = ws.receive_json()
        assert first['event'] == 'query.started'
        qid = first['data']['query_id']
    # 连接已断开:服务端 finally 应取消查询;递归 CTE 不取消会跑到天荒地老。
    # 状态可能是 cancelled(任务被杀)或 error(驱动 interrupt 先生效),两者都算停。
    for _ in range(100):
        ctx = query_service._queries.get(qid)   # 读内部状态验证取消语义
        if ctx is None or ctx.status != 'running':
            break
        time.sleep(0.05)
    ctx = query_service._queries.get(qid)
    assert ctx is None or ctx.status != 'running'


async def test_cancel_no_reconnect_for_uncached_conn(monkeypatch):
    """取消一个驱动已不在缓存里的查询:杀掉任务即可,绝不为取消去新建连接。
    用 manager.get 的调用记录断言(旧实现会调 manager.get → 本用例变红)。"""
    from backend.app.services.connection_manager import manager
    from backend.app.services.query_service import QueryCtx, query_service

    get_calls: list[str] = []
    orig_get = manager.get

    async def spy_get(conn_id: str):
        get_calls.append(conn_id)
        return await orig_get(conn_id)

    monkeypatch.setattr(manager, 'get', spy_get)

    async def forever():
        await asyncio.sleep(3600)

    ctx = QueryCtx(query_id='q-orphan', conn_id='no-such-conn', stmt='SELECT 1')
    ctx.task = asyncio.create_task(forever())
    query_service._queries['q-orphan'] = ctx
    try:
        await query_service.cancel('q-orphan')
        assert get_calls == []                        # 取消路径不再触碰 manager.get
        assert 'no-such-conn' not in manager._drivers
        with pytest.raises(asyncio.CancelledError):
            await ctx.task                            # 取消已送达并生效
    finally:
        query_service._queries.pop('q-orphan', None)


# ── Redis / Mongo 入口校验 ──

async def test_redis_metadata_bad_path():
    d = create_driver({'type': 'redis', 'name': 'r'})
    with pytest.raises(QueryError):
        await d.metadata('not-a-db-path')   # 旧实现 int('ot-a-db-path') ValueError → 502


def test_mongo_bad_placeholder_is_queryerror():
    from backend.app.drivers.mongo_driver import parse_shell
    with pytest.raises(QueryError):
        parse_shell("db.c.find({a: '$$date:not-a-date'})")
    with pytest.raises(QueryError):
        parse_shell("db.c.find({a: '$$oid:bad'})")
    spec = parse_shell("db.c.find({a: 1}).limit(5)")
    assert spec['collection'] == 'c' and spec['limit'] == 5
