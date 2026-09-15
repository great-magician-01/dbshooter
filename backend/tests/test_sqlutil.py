"""SQL 拆分与只读判定。"""
from backend.app.drivers.sqlutil import is_query, split_sql


def test_split_basic():
    assert split_sql('SELECT 1; SELECT 2;') == ['SELECT 1', 'SELECT 2']
    assert split_sql('SELECT 1') == ['SELECT 1']
    assert split_sql('  \n ; \n') == []


def test_split_semicolon_in_string():
    assert split_sql("SELECT 'a;b'; SELECT 2") == ["SELECT 'a;b'", 'SELECT 2']


def test_split_semicolon_in_comment():
    assert split_sql('-- hi;there\nSELECT 1; /* x;y */ SELECT 2') == \
        ['-- hi;there\nSELECT 1', '/* x;y */ SELECT 2']


def test_split_escaped_quote():
    assert split_sql(r"SELECT 'it\'s;x'; SELECT 2") == [r"SELECT 'it\'s;x'", 'SELECT 2']


def test_is_query():
    assert is_query('SELECT 1')
    assert is_query('  (select 1)')
    assert is_query('WITH x AS (SELECT 1) SELECT * FROM x')
    assert not is_query('DELETE FROM t')
    assert not is_query('INSERT INTO t VALUES (1)')
