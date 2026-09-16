"""SQL 工具:多语句拆分(忽略字符串/注释内的分号)+ 结果值 JSON 化。"""
from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Mapping, Set
from typing import Any

from .base import READONLY_PREFIXES


def split_sql(script: str) -> list[str]:
    """按分号拆分脚本,跳过单双引号字符串、反引号标识符、行/块注释。"""
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(script)
    state = None  # None | "'" | '"' | '`' | '--' | '/*'
    while i < n:
        ch = script[i]
        nxt = script[i + 1] if i + 1 < n else ''
        if state is None:
            if ch in ('\'', '"', '`'):
                state = ch
            elif ch == '-' and nxt == '-':
                state = '--'
            elif ch == '/' and nxt == '*':
                state = '/*'
            elif ch == ';':
                s = ''.join(buf).strip()
                if s:
                    stmts.append(s)
                buf = []
                i += 1
                continue
        elif state in ('\'', '"', '`'):
            if ch == '\\' and state != '`':
                buf.append(ch)
                i += 1
                if i < n:
                    buf.append(script[i])
                    i += 1
                continue
            if ch == state:
                state = None
        elif state == '--':
            if ch == '\n':
                state = None
        elif state == '/*':
            if ch == '*' and nxt == '/':
                buf.append('*/')
                i += 2
                state = None
                continue
        buf.append(ch)
        i += 1
    s = ''.join(buf).strip()
    if s:
        stmts.append(s)
    return stmts


def is_query(stmt: str) -> bool:
    first = stmt.lstrip(' \t\r\n(-').split(None, 1)[0].lower() if stmt.strip() else ''
    return first in READONLY_PREFIXES


def jsonable(v: Any) -> Any:
    """驱动返回值 → JSON 可序列化原语。

    DB-API/驱动会返回 datetime / Decimal / UUID / bytes 等原生对象,
    WS 通道走原生 json.dumps 会直接抛 "not JSON serializable",
    统一在驱动出口转成字符串,让 WS / REST / CSV 各通道表示一致。
    """
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time,
                      datetime.timedelta, decimal.Decimal, uuid.UUID)):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        try:
            return b.decode('utf-8')       # 文本型 BLOB 直接还原
        except UnicodeDecodeError:
            return b.hex()                 # 真二进制以十六进制展示
    if isinstance(v, Mapping):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, Set):
        return sorted(jsonable(x) for x in v)
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v
