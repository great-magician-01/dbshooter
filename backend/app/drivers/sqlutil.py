"""SQL 工具:多语句拆分(忽略字符串/注释内的分号)+ 结果值 JSON 化。"""
from __future__ import annotations

import datetime
import decimal
import math
import re
import uuid
from collections.abc import Mapping, Set
from typing import Any

from .base import READONLY_PREFIXES, first_keyword

# PG dollar-quoting 开界定符:$$ 或 $tag$(函数体常用,内含分号不可拆)
_DOLLAR_OPEN = re.compile(r'\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$')


def split_sql(script: str, *, backslash_escapes: bool = True) -> list[str]:
    """按分号拆分脚本,跳过字符串/标识符/注释/dollar-quoting 内的分号。

    backslash_escapes:引号字符串内反斜杠是否当转义。MySQL 默认开;
    SQLite 与 PG(standard_conforming_strings=on)是字面量,须关掉,
    否则 `'a\\'; SELECT 1` 会被误并成一条语句。
    """
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(script)
    state: str | None = None   # None | "'" | '"' | '`' | '--' | '/*' | '$'
    dollar = ''               # state == '$' 时的界定符($$ 或 $tag$)
    while i < n:
        ch = script[i]
        nxt = script[i + 1] if i + 1 < n else ''
        if state is None:
            if ch in ('\'', '"', '`'):
                state = ch
            elif ch == '$':
                m = _DOLLAR_OPEN.match(script, i)
                if m:
                    dollar = m.group()
                    buf.append(dollar)
                    i = m.end()
                    state = '$'
                    continue
                # 普通含 $ 的标识符/占位符($1 等)按原样走
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
            if ch == '\\' and state != '`' and backslash_escapes:
                buf.append(ch)
                i += 1
                if i < n:
                    buf.append(script[i])
                    i += 1
                continue
            if ch == state:
                state = None
        elif state == '$':
            if script.startswith(dollar, i):
                buf.append(dollar)
                i += len(dollar)
                state = None
                continue
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
    return first_keyword(stmt) in READONLY_PREFIXES


def jsonable(v: Any) -> Any:
    """驱动返回值 → JSON 可序列化原语。

    DB-API/驱动会返回 datetime / Decimal / UUID / bytes 等原生对象,
    WS 通道走原生 json.dumps 会直接抛 "not JSON serializable",
    统一在驱动出口转成字符串,让 WS / REST / CSV 各通道表示一致。
    非有限浮点(inf/-inf/nan)转字符串:WS 侧 json.dumps 会产出非法 JSON
    (Infinity/NaN 字面量),浏览器 JSON.parse 直接抛错导致查询悬死;
    REST 侧 allow_nan=False 则整包 500。
    """
    if v is None or isinstance(v, (str, int, bool)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)   # 'inf' / '-inf' / 'nan'
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
