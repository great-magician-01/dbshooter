"""Mongo shell 解析与 Redis 回包格式化(纯函数,无需真实服务)。"""
import pytest

from backend.app.drivers import QueryError
from backend.app.drivers.mongo_driver import parse_args, parse_shell
from backend.app.drivers.redis_driver import format_command_result


# ── mongo parse ──

def test_parse_find_with_chain():
    spec = parse_shell(
        "db.events.find({ type: 'page_view', ts: { $gte: ISODate('2026-09-01T00:00:00Z') } })"
        ".sort({ ts: -1 }).limit(50)")
    assert spec['collection'] == 'events' and spec['method'] == 'find'
    assert spec['args'][0]['type'] == 'page_view'
    assert spec['sort'] == [('ts', -1)]
    assert spec['limit'] == 50
    from datetime import datetime
    assert isinstance(spec['args'][0]['ts']['$gte'], datetime)


def test_parse_objectid():
    args = parse_args("{ _id: ObjectId('66e9a1f2c8d0e1f2a3b4c5d6') }")
    from bson import ObjectId
    assert isinstance(args[0]['_id'], ObjectId)


def test_parse_count_and_skip():
    spec = parse_shell('db.users.countDocuments({ status: 1 })')
    assert spec['method'] == 'countDocuments'
    spec = parse_shell('db.users.find({}).skip(10).limit(5)')
    assert spec['skip'] == 10 and spec['limit'] == 5


def test_parse_invalid():
    with pytest.raises(QueryError):
        parse_shell('SELECT * FROM t')
    with pytest.raises(QueryError):
        parse_shell('db.t.drop()')   # 不支持的方法


# ── redis format ──

def test_format_scalars():
    assert format_command_result(None) == ['(nil)']
    assert format_command_result('OK') == ['OK']
    assert format_command_result(b'abc') == ['abc']


def test_format_list_and_dict():
    assert format_command_result(['a', 'b']) == ['1) a', '2) b']
    assert format_command_result({'f1': 'v1'}) == ['f1 => v1']
    assert format_command_result([]) == ['(empty)']


def test_format_nested():
    lines = format_command_result([['x', 1]])
    assert lines == ['1)', '  1) x', '  2) 1']
