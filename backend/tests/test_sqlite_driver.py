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


async def test_ddl(sqlite_db):
    d = make(sqlite_db)
    ddl = await d.ddl(['users'])
    assert 'CREATE TABLE users' in ddl
    await d.close()


async def test_unsupported_type():
    with pytest.raises(QueryError):
        create_driver({'type': 'oracle'})
