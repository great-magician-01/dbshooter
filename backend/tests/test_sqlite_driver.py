"""SQLite 驱动:真实库文件端到端(连接/元数据/执行/截断/只读/DDL)。"""
import pytest

from backend.app.drivers import QueryError, ReadonlyViolation, create_driver


def make(sqlite_db, readonly=False):
    return create_driver({'type': 'sqlite', 'name': 't',
                          'params': {'path': sqlite_db}, 'readonly': readonly})


async def test_test_and_metadata(sqlite_db):
    d = make(sqlite_db)
    ok, msg = await d.test()
    assert ok and 'SQLite' in msg

    top = await d.metadata('')
    assert top[0].label == 'main' and top[0].has_children

    tables = await d.metadata('main')
    by_label = {n.label: n for n in tables}
    assert by_label['users'].kind == 'table'
    assert by_label['v_users'].kind == 'view'

    cols = await d.metadata('main.users')
    assert [c.label for c in cols] == ['id', 'name', 'city']
    assert cols[0].extra['pk'] is True
    await d.close()


async def test_execute_rows_and_affected(sqlite_db):
    d = make(sqlite_db)
    res = await d.execute("SELECT * FROM users WHERE city='上海'")
    assert res[0].kind == 'rows'
    assert res[0].columns[0]['name'] == 'id'
    assert res[0].rows[0][1] == '张三'

    res = await d.execute("INSERT INTO users(name) VALUES ('赵六'); UPDATE users SET city='广州' WHERE name='赵六'")
    assert [r.kind for r in res] == ['affected', 'affected']
    assert res[0].affected == 1 and res[1].affected == 1
    await d.close()


async def test_execute_truncation(sqlite_db):
    d = make(sqlite_db)
    res = await d.execute('SELECT * FROM users', limit=2)
    assert res[0].truncated is True and len(res[0].rows) == 2
    await d.close()


async def test_execute_error_not_raise(sqlite_db):
    d = make(sqlite_db)
    res = await d.execute('SELECT * FROM no_such_table')
    assert res[0].error
    await d.close()


async def test_readonly(sqlite_db):
    d = make(sqlite_db, readonly=True)
    with pytest.raises(ReadonlyViolation):
        await d.execute('DELETE FROM users')
    res = await d.execute('SELECT 1')  # 查询放行
    assert res[0].kind == 'rows'
    await d.close()


async def test_readonly_blocks_pragma_writes(sqlite_db):
    """首词拦截之外的写型 PRAGMA:mode=ro 在内核层兜底,user_version 不得被改。"""
    import sqlite3
    d = make(sqlite_db, readonly=True)
    res = await d.execute('PRAGMA user_version = 42')
    assert res[0].kind == 'error' and res[0].error is not None and 'readonly' in res[0].error
    await d.close()
    with sqlite3.connect(sqlite_db) as c:
        assert c.execute('PRAGMA user_version').fetchone()[0] == 0


async def test_metadata_quoted_table_name(sqlite_db):
    """PRAGMA 不支持参数绑定,表名含引号时靠标识符翻倍,不应抛语法错误。"""
    d = make(sqlite_db)
    cols = await d.metadata('main.x") FROM t--')
    assert cols == []   # 只是"没有这个表",不再是 OperationalError
    await d.close()


async def test_ddl(sqlite_db):
    d = make(sqlite_db)
    ddl = await d.ddl(['users'])
    assert 'CREATE TABLE users' in ddl
    await d.close()


async def test_table_columns(sqlite_db):
    """结构页签:列名/类型/主键/可空/默认值/序号(PRAGMA 实际值口径)。"""
    d = make(sqlite_db)
    cols = await d.table_columns('main.users')
    assert [c.name for c in cols] == ['id', 'name', 'city']
    id_col = cols[0]
    assert id_col.type == 'INTEGER' and id_col.pk is True and id_col.ordinal == 0
    # INTEGER PRIMARY KEY 是 rowid 别名,未显式 NOT NULL 时 pragma 报 notnull=0
    assert id_col.nullable is True and id_col.default is None

    order_cols = {c.name: c for c in await d.table_columns('main.orders')}
    assert order_cols['amount'].default == '0'
    assert order_cols['user_id'].pk is False
    # 视图也有列
    view_cols = await d.table_columns('main.v_users')
    assert [c.name for c in view_cols] == ['name']
    await d.close()


async def test_table_relations_out_and_in(sqlite_db):
    """ER 页签:orders 出站引用 users;users 入站被 orders 引用。"""
    d = make(sqlite_db)
    out = await d.table_relations('main.orders')
    assert len(out) == 1
    rel = out[0]
    assert rel.direction == 'out' and rel.table == 'orders' and rel.column == 'user_id'
    assert rel.ref_table == 'users' and rel.ref_column == 'id' and rel.seq == 0

    in_rels = await d.table_relations('main.users')
    assert len(in_rels) == 1
    rel = in_rels[0]
    assert rel.direction == 'in' and rel.table == 'orders' and rel.column == 'user_id'
    assert rel.ref_table == 'users' and rel.ref_column == 'id'
    await d.close()


async def test_table_relations_self_reference(sqlite_db):
    """自引用 FK 只记一条(direction='out'),不重复计入站。"""
    d = make(sqlite_db)
    rels = await d.table_relations('main.employees')
    assert len(rels) == 1
    rel = rels[0]
    assert rel.direction == 'out' and rel.table == 'employees'
    assert rel.ref_table == 'employees' and rel.ref_column == 'id'
    await d.close()


async def test_table_relations_composite_fk(sqlite_db):
    """复合 FK:同约束名多行,seq 标列序;被引侧对称可见。"""
    d = make(sqlite_db)
    rels = await d.table_relations('main.children')
    assert len(rels) == 2
    assert {r.name for r in rels} == {'fk_children_0'}
    by_seq = {r.seq: r for r in rels}
    assert by_seq[0].column == 'x' and by_seq[0].ref_column == 'a'
    assert by_seq[1].column == 'y' and by_seq[1].ref_column == 'b'

    parent_rels = await d.table_relations('main.parents')
    assert len(parent_rels) == 2
    assert all(r.direction == 'in' and r.table == 'children' for r in parent_rels)
    # 视图无关系
    assert await d.table_relations('main.v_users') == []
    await d.close()


async def test_table_relations_implicit_pk_column(sqlite_db):
    """REFERENCES 省略被引列(to 为 NULL)时回退到被引表主键列。"""
    import sqlite3
    d = make(sqlite_db)
    with sqlite3.connect(sqlite_db) as c:
        c.execute('CREATE TABLE logs(id INTEGER PRIMARY KEY, uid INTEGER REFERENCES users)')
        c.commit()
    rels = await d.table_relations('main.logs')
    assert len(rels) == 1
    assert rels[0].ref_table == 'users' and rels[0].ref_column == 'id'
    await d.close()


async def test_unsupported_type():
    with pytest.raises(QueryError):
        create_driver({'type': 'oracle'})
