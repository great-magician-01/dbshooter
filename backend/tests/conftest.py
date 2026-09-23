"""测试基座:独立临时数据目录 + 固定密钥(必须在导入任何 backend.app 模块前设置)。"""
from __future__ import annotations

import os
import sqlite3
import tempfile

_TMP = tempfile.mkdtemp(prefix='dbshooter-test-')
os.environ['DBSHOOTER_DATA_DIR'] = _TMP
os.environ['DBSHOOTER_SECRET'] = 'test-secret-key'
# 隔离外部环境:config.ACCESS_TOKEN 在 import 时读环境变量,
# 带 DBSHOOTER_TOKEN 的 shell 里跑测试会全线 401
os.environ.pop('DBSHOOTER_TOKEN', None)

import pytest  # noqa: E402


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def sqlite_db(tmp_path) -> str:
    """建一个带样例数据的目标 SQLite 库,返回文件路径。"""
    p = tmp_path / 'demo.db'
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, city TEXT);
        INSERT INTO users(name, city) VALUES ('张三','上海'),('李四','北京'),('王五','深圳');
        CREATE VIEW v_users AS SELECT name FROM users;
        -- ER/结构测试:普通 FK(出站 orders→users,入站 users←orders)
        CREATE TABLE orders(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id),
                            amount REAL DEFAULT 0);
        -- 自引用 FK
        CREATE TABLE employees(id INTEGER PRIMARY KEY,
                               manager_id INTEGER REFERENCES employees(id));
        -- 复合 FK
        CREATE TABLE parents(a INTEGER, b INTEGER, PRIMARY KEY(a, b));
        CREATE TABLE children(x INTEGER, y INTEGER,
                              FOREIGN KEY(x, y) REFERENCES parents(a, b));
    """)
    c.commit()
    c.close()
    return str(p)


@pytest.fixture()
def sqlite_conn_id(client, sqlite_db) -> str:
    r = client.post('/api/connections', json={
        'name': '测试库', 'type': 'sqlite', 'params': {'path': sqlite_db}})
    assert r.status_code == 200, r.text
    return r.json()['item']['id']
