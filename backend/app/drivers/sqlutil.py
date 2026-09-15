"""SQL 工具:多语句拆分(忽略字符串/注释内的分号)。"""
from __future__ import annotations

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
