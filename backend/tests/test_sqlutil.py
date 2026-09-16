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
