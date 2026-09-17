"""SQL 拆分、只读判定与结果值 JSON 化。"""
import datetime
import decimal
import uuid

from backend.app.drivers.sqlutil import is_query, jsonable, split_sql


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


def test_split_backslash_dialects():
    """反斜杠转义是方言差异:MySQL 当转义,PG(standard_conforming_strings)/SQLite 当字面量。"""
    stmt = r"SELECT 'a\'; SELECT 'b'"
    # MySQL: \' 被转义 → 字符串吞掉引号,整段是一条语句
    assert split_sql(stmt, backslash_escapes=True) == [stmt]
    # PG/SQLite: \ 是字面量,'a\' 已经闭合 → 两条语句
    assert split_sql(stmt, backslash_escapes=False) == ["SELECT 'a\\'", "SELECT 'b'"]


def test_split_dollar_quoting():
    """PG 函数体常用 $$...$$,内部分号不可拆。"""
    fn = ('CREATE FUNCTION f() RETURNS int AS $$\n'
          'BEGIN\n  PERFORM 1;\n  RETURN 1;\nEND\n$$ LANGUAGE plpgsql')
    assert split_sql(fn + '; SELECT 2') == [fn, 'SELECT 2']
    # 带 tag 的界定符:tag 不匹配不闭合
    tagged = 'SELECT $tag$a;b$tag$'
    assert split_sql(tagged + '; SELECT 2') == [tagged, 'SELECT 2']
    # 未配对的 $$ 整段保留(交由数据库报错,而不是拆烂)
    assert split_sql('SELECT $$abc;def') == ['SELECT $$abc;def']
    # 占位符 $1 等不受影响
    assert split_sql('SELECT $1; SELECT $2') == ['SELECT $1', 'SELECT $2']


def test_is_query():
    assert is_query('SELECT 1')
    assert is_query('  (select 1)')
    assert is_query('WITH x AS (SELECT 1) SELECT * FROM x')
    assert not is_query('DELETE FROM t')
    assert not is_query('INSERT INTO t VALUES (1)')


def test_jsonable_primitives_pass_through():
    assert jsonable(None) is None
    assert jsonable(True) is True
    assert jsonable(1) == 1 and jsonable(1.5) == 1.5
    assert jsonable('文本') == '文本'


def test_jsonable_datetime_like():
    # MySQL DATETIME / DATE,PG interval 等驱动原生类型 → 可读字符串
    assert jsonable(datetime.datetime(2026, 9, 17, 10, 30)) == '2026-09-17 10:30:00'
    assert jsonable(datetime.date(2026, 9, 17)) == '2026-09-17'
    assert jsonable(datetime.time(10, 30)) == '10:30:00'
    assert jsonable(datetime.timedelta(hours=1)) == '1:00:00'


def test_jsonable_decimal_uuid():
    assert jsonable(decimal.Decimal('1.10')) == '1.10'  # 不走 float,保留精度
    u = uuid.UUID('12345678-1234-5678-1234-567812345678')
    assert jsonable(u) == str(u)


def test_jsonable_bytes_text_vs_binary():
    assert jsonable('文本'.encode()) == '文本'   # 文本型 BLOB 还原
    assert jsonable(b'\x00\xff') == '00ff'      # 真二进制十六进制展示


def test_jsonable_containers_recurse():
    assert jsonable([datetime.date(2026, 1, 1), (decimal.Decimal('2'),)]) == \
        ['2026-01-01', ['2']]
    assert jsonable({'b', 'a'}) == ['a', 'b']   # MySQL SET 列,排序保证稳定
    assert jsonable({'k': b'\xff'}) == {'k': 'ff'}
