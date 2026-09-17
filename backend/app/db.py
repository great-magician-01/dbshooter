"""应用元数据存储:内置 SQLite(连接 / AI Provider / AI 会话 / 工作区页签 / 查询历史 / settings)。

同步 sqlite3 + 全局锁:元数据操作极低频,无需异步驱动,换来的是简单可靠。
敏感字段(password / api_key)在写入前经 security.encrypt 加密。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from . import config, security

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  host TEXT DEFAULT '', port INTEGER, database TEXT DEFAULT '', username TEXT DEFAULT '',
  password_enc TEXT DEFAULT '',
  params_json TEXT DEFAULT '{}',
  readonly INTEGER DEFAULT 0,
  sort INTEGER DEFAULT 0,
  created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS ai_providers(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  api_key_enc TEXT DEFAULT '',
  model TEXT NOT NULL,
  is_active INTEGER DEFAULT 0,
  created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS ai_sessions(
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  connection_id TEXT,
  created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS ai_messages(
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES ai_sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  tokens INTEGER, elapsed_ms INTEGER,
  created_at TEXT);

CREATE TABLE IF NOT EXISTS editor_tabs(
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  connection_id TEXT,
  context_json TEXT DEFAULT '{}',
  content TEXT DEFAULT '',
  sort INTEGER DEFAULT 0,
  is_active INTEGER DEFAULT 0,
  updated_at TEXT);

CREATE TABLE IF NOT EXISTS query_history(
  id TEXT PRIMARY KEY,
  connection_id TEXT,
  stmt TEXT,
  elapsed_ms INTEGER, row_count INTEGER, status TEXT,
  executed_at TEXT);

CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY, value TEXT);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute('PRAGMA journal_mode=WAL')
        _conn.execute('PRAGMA foreign_keys=ON')
        _conn.executescript(SCHEMA)
    return _conn


def q(sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def _must(sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any]:
    """断言单行存在的回读:仅用于 INSERT/UPDATE 刚落库后的按 id 回读。"""
    row = one(sql, args)
    assert row is not None, f'写入后回读失败: {sql}'
    return row


def run(sql: str, args: tuple[Any, ...] = ()) -> None:
    with _lock:
        conn().execute(sql, args)
        conn().commit()


def reset_for_tests() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


# ──────────────────────── 连接 ────────────────────────

def _conn_out(row: dict[str, Any]) -> dict[str, Any]:
    """对外输出:密文不下发,只标记是否已设置。"""
    row = dict(row)
    row['has_password'] = bool(row.pop('password_enc'))
    row['params'] = json.loads(row.pop('params_json') or '{}')
    row['readonly'] = bool(row['readonly'])
    return row


def list_connections() -> list[dict[str, Any]]:
    return [_conn_out(r) for r in q('SELECT * FROM connections ORDER BY sort, created_at')]


def get_connection(cid: str) -> dict[str, Any] | None:
    """内部使用:含解密后的密码,绝不下发前端。"""
    row = one('SELECT * FROM connections WHERE id=?', (cid,))
    if not row:
        return None
    row['password'] = security.decrypt(row.pop('password_enc') or '')
    row['params'] = json.loads(row.pop('params_json') or '{}')
    row['readonly'] = bool(row['readonly'])
    return row


def create_connection(d: dict[str, Any]) -> dict[str, Any]:
    cid = d.get('id') or new_id()
    t = now()
    # 可选字段经 pydantic 缺省为 None,落库统一成空值
    run('INSERT INTO connections(id,name,type,host,port,database,username,password_enc,'
        'params_json,readonly,sort,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (cid, d['name'], d['type'], d.get('host') or '', d.get('port'),
         d.get('database') or '', d.get('username') or '',
         security.encrypt(d.get('password') or ''),
         json.dumps(d.get('params') or {}), int(bool(d.get('readonly'))),
         d.get('sort', 0), t, t))
    return _conn_out(_must('SELECT * FROM connections WHERE id=?', (cid,)))


def update_connection(d: dict[str, Any]) -> dict[str, Any] | None:
    cid = d['id']
    old = one('SELECT * FROM connections WHERE id=?', (cid,))
    if not old:
        return None
    # 合并语义:字段值为 None = 未传 = 沿用旧值(与 CLI 的更新语义一致)。
    # 旧实现是全量覆盖,调用方漏传 readonly/params 会静默清零 —— 只读连接被意外解除。
    pwd = security.encrypt(d['password']) if d.get('password') else old['password_enc']
    host = old['host'] if d.get('host') is None else d['host']
    port = old['port'] if d.get('port') is None else d['port']
    database = old['database'] if d.get('database') is None else d['database']
    username = old['username'] if d.get('username') is None else d['username']
    params = json.loads(old['params_json'] or '{}') if d.get('params') is None else d['params']
    readonly = bool(old['readonly']) if d.get('readonly') is None else d['readonly']
    run('UPDATE connections SET name=?,type=?,host=?,port=?,database=?,username=?,'
        'password_enc=?,params_json=?,readonly=?,updated_at=? WHERE id=?',
        (d['name'], d['type'], host, port, database, username, pwd,
         json.dumps(params or {}), int(readonly), now(), cid))
    return _conn_out(_must('SELECT * FROM connections WHERE id=?', (cid,)))


def delete_connection(cid: str) -> None:
    run('DELETE FROM connections WHERE id=?', (cid,))


# ──────────────────────── AI Provider ────────────────────────

def _provider_out(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row['has_api_key'] = bool(row.pop('api_key_enc'))
    row['is_active'] = bool(row['is_active'])
    return row


def list_providers() -> list[dict[str, Any]]:
    return [_provider_out(r) for r in q('SELECT * FROM ai_providers ORDER BY created_at')]


def get_provider(pid: str) -> dict[str, Any] | None:
    row = one('SELECT * FROM ai_providers WHERE id=?', (pid,))
    if not row:
        return None
    row['api_key'] = security.decrypt(row.pop('api_key_enc') or '')
    row['is_active'] = bool(row['is_active'])
    return row


def get_active_provider() -> dict[str, Any] | None:
    row = one('SELECT * FROM ai_providers WHERE is_active=1')
    return get_provider(row['id']) if row else None


def create_provider(d: dict[str, Any]) -> dict[str, Any]:
    pid = d.get('id') or new_id()
    t = now()
    run('INSERT INTO ai_providers(id,name,base_url,api_key_enc,model,is_active,created_at,updated_at)'
        ' VALUES(?,?,?,?,?,?,?,?)',
        (pid, d['name'], d['base_url'], security.encrypt(d.get('api_key', '')),
         d['model'], int(d.get('is_active', False)), t, t))
    if d.get('is_active'):
        set_active_provider(pid)
    return _provider_out(_must('SELECT * FROM ai_providers WHERE id=?', (pid,)))


def update_provider(d: dict[str, Any]) -> dict[str, Any] | None:
    pid = d['id']
    old = one('SELECT * FROM ai_providers WHERE id=?', (pid,))
    if not old:
        return None
    key = security.encrypt(d['api_key']) if d.get('api_key') else old['api_key_enc']
    run('UPDATE ai_providers SET name=?,base_url=?,api_key_enc=?,model=?,updated_at=? WHERE id=?',
        (d['name'], d['base_url'], key, d['model'], now(), pid))
    return _provider_out(_must('SELECT * FROM ai_providers WHERE id=?', (pid,)))


def delete_provider(pid: str) -> None:
    run('DELETE FROM ai_providers WHERE id=?', (pid,))


def set_active_provider(pid: str) -> None:
    """全表唯一生效:单事务先清后设。"""
    with _lock:
        c = conn()
        c.execute('BEGIN')
        try:
            c.execute('UPDATE ai_providers SET is_active=0')
            c.execute('UPDATE ai_providers SET is_active=1,updated_at=? WHERE id=?', (now(), pid))
            c.execute('COMMIT')
        except Exception:
            c.execute('ROLLBACK')
            raise


# ──────────────────────── AI 会话与消息 ────────────────────────

def list_sessions() -> list[dict[str, Any]]:
    # now() 精度为秒,同一秒内的并列用 rowid 打破(最新插入在前),否则顺序不稳定
    return q('SELECT * FROM ai_sessions ORDER BY updated_at DESC, rowid DESC')


def create_session(d: dict[str, Any]) -> dict[str, Any]:
    sid = d.get('id') or new_id()
    t = now()
    run('INSERT INTO ai_sessions(id,title,connection_id,created_at,updated_at) VALUES(?,?,?,?,?)',
        (sid, d.get('title') or '新会话', d.get('connection_id'), t, t))
    return _must('SELECT * FROM ai_sessions WHERE id=?', (sid,))


def rename_session(sid: str, title: str) -> None:
    run('UPDATE ai_sessions SET title=?,updated_at=? WHERE id=?', (title, now(), sid))


def delete_session(sid: str) -> None:
    run('DELETE FROM ai_messages WHERE session_id=?', (sid,))
    run('DELETE FROM ai_sessions WHERE id=?', (sid,))


def add_message(session_id: str, role: str, content: str,
                tokens: int | None = None, elapsed_ms: int | None = None) -> dict[str, Any]:
    mid = new_id()
    run('INSERT INTO ai_messages(id,session_id,role,content,tokens,elapsed_ms,created_at)'
        ' VALUES(?,?,?,?,?,?,?)',
        (mid, session_id, role, content, tokens, elapsed_ms, now()))
    run('UPDATE ai_sessions SET updated_at=? WHERE id=?', (now(), session_id))
    return _must('SELECT * FROM ai_messages WHERE id=?', (mid,))


def list_messages(session_id: str) -> list[dict[str, Any]]:
    return q('SELECT * FROM ai_messages WHERE session_id=? ORDER BY created_at, rowid', (session_id,))


# ──────────────────────── 工作区页签 ────────────────────────

def list_tabs() -> list[dict[str, Any]]:
    rows = q('SELECT * FROM editor_tabs ORDER BY sort, rowid')
    for r in rows:
        r['context'] = json.loads(r.pop('context_json') or '{}')
        r['is_active'] = bool(r['is_active'])
    return rows


def save_tab(d: dict[str, Any]) -> dict[str, Any]:
    """upsert:不存在则插入,存在则更新内容/标题/上下文。"""
    t = now()
    exists = one('SELECT id FROM editor_tabs WHERE id=?', (d['id'],))
    if exists:
        run('UPDATE editor_tabs SET type=?,title=?,connection_id=?,context_json=?,content=?,'
            'sort=?,updated_at=? WHERE id=?',
            (d['type'], d['title'], d.get('connection_id'), json.dumps(d.get('context') or {}),
             d.get('content', ''), d.get('sort', 0), t, d['id']))
    else:
        run('INSERT INTO editor_tabs(id,type,title,connection_id,context_json,content,sort,'
            'is_active,updated_at) VALUES(?,?,?,?,?,?,?,0,?)',
            (d['id'], d['type'], d['title'], d.get('connection_id'),
             json.dumps(d.get('context') or {}), d.get('content', ''), d.get('sort', 0), t))
    row = _must('SELECT * FROM editor_tabs WHERE id=?', (d['id'],))
    row['context'] = json.loads(row.pop('context_json') or '{}')
    row['is_active'] = bool(row['is_active'])
    return row


def delete_tab(tid: str) -> None:
    run('DELETE FROM editor_tabs WHERE id=?', (tid,))


def save_tab_order(ids: list[str], active_id: str | None) -> None:
    with _lock:
        c = conn()
        c.execute('BEGIN')
        try:
            c.execute('UPDATE editor_tabs SET is_active=0')
            for i, tid in enumerate(ids):
                c.execute('UPDATE editor_tabs SET sort=? WHERE id=?', (i, tid))
            if active_id:
                c.execute('UPDATE editor_tabs SET is_active=1 WHERE id=?', (active_id,))
            c.execute('COMMIT')
        except Exception:
            c.execute('ROLLBACK')
            raise


# ──────────────────────── 查询历史 ────────────────────────

def add_history(connection_id: str, stmt: str, elapsed_ms: int,
                row_count: int, status: str) -> None:
    run('INSERT INTO query_history(id,connection_id,stmt,elapsed_ms,row_count,status,executed_at)'
        ' VALUES(?,?,?,?,?,?,?)',
        (new_id(), connection_id, stmt[:4000], elapsed_ms, row_count, status, now()))


def list_history(limit: int = 100) -> list[dict[str, Any]]:
    # 同一秒内的并列用 rowid 打破(最新插入在前),与 list_sessions 一致
    return q('SELECT * FROM query_history ORDER BY executed_at DESC, rowid DESC LIMIT ?', (limit,))


# ──────────────────────── settings ────────────────────────

def get_settings() -> dict[str, str]:
    return {r['key']: r['value'] for r in q('SELECT key,value FROM settings')}


def save_settings(values: dict[str, Any]) -> None:
    with _lock:
        c = conn()
        for k, v in values.items():
            c.execute('INSERT INTO settings(key,value) VALUES(?,?) '
                      'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (k, str(v)))
        c.commit()
