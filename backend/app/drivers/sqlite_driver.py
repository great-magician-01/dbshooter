"""SQLite 驱动:零配置本地文件库,aiosqlite 异步执行。"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import aiosqlite

from .base import DriverBase, ExecResult, MetaNode, QueryError, ensure_writable, register
from .sqlutil import jsonable, split_sql


@register
class SqliteDriver(DriverBase):
    kind = 'sqlite'
    editor_mode = 'sql'

    def __init__(self, cfg: dict[str, Any]):
        super().__init__(cfg)
        self.path = (cfg.get('params') or {}).get('path') or cfg.get('database') or ':memory:'
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self.conn is None:
            # 只读连接:file: URI + mode=ro,在 SQLite 内核层只读,
            # PRAGMA 写入(如 user_version)也无法绕过首词拦截
            if bool(self.cfg.get('readonly')) and self.path != ':memory:':
                uri = f'file:{quote(self.path)}?mode=ro'
                self.conn = await aiosqlite.connect(uri, uri=True)
            else:
                self.conn = await aiosqlite.connect(self.path)

    async def test(self) -> tuple[bool, str]:
        t0 = time.monotonic()
        await self.connect()
        assert self.conn is not None  # connect() 保证已建立
        async with self.conn.execute('SELECT sqlite_version()') as cur:
            row = await cur.fetchone()
            assert row is not None  # SELECT sqlite_version() 必有返回
        return True, f'SQLite {row[0]} · {int((time.monotonic()-t0)*1000)} ms'

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def metadata(self, path: str) -> list[MetaNode]:
        await self.connect()
        assert self.conn is not None
        parts = [p for p in path.split('.') if p]
        if not parts:
            return [MetaNode(path='main', label='main', kind='database', has_children=True)]
        if len(parts) == 1:
            nodes = []
            async with self.conn.execute(
                    "SELECT name, type FROM sqlite_master WHERE type IN ('table','view')"
                    " AND name NOT LIKE 'sqlite_%' ORDER BY type, name") as cur:
                async for name, typ in cur:
                    nodes.append(MetaNode(path=f'main.{name}', label=name, kind=typ,
                                          has_children=True))
            return nodes
        table = parts[1]
        cols = []
        # PRAGMA 不支持参数绑定,标识符双引号翻倍防注入(表名含 " 时不再语法错)
        safe = table.replace('"', '""')
        async with self.conn.execute(f'PRAGMA table_info("{safe}")') as cur:
            async for cid, name, ctype, notnull, dflt, pk in cur:
                cols.append(MetaNode(path=f'{path}.{name}', label=name, kind='column',
                                     extra={'type': ctype or '', 'pk': bool(pk),
                                            'nullable': not notnull}))
        return cols

    async def ai_namespaces(self) -> list[str]:
        return ['main']

    async def ai_tables(self, namespace: str | None = None) -> list[MetaNode]:
        # SQLite 只有 main 一个命名空间
        return await self.metadata('main')

    async def ddl(self, tables: list[str]) -> str:
        await self.connect()
        assert self.conn is not None
        out = []
        for t in tables:
            name = t.split('.')[-1]
            async with self.conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name=? AND type IN ('table','view')",
                    (name,)) as cur:
                row = await cur.fetchone()
            if row and row[0]:
                out.append(row[0] + ';')
        return '\n\n'.join(out)

    async def execute(self, stmt: str, limit: int = 500,
                      schema: str | None = None) -> list[ExecResult]:
        # SQLite 单库(main)即全部命名空间,无 schema 会话概念
        await self.connect()
        assert self.conn is not None
        readonly = bool(self.cfg.get('readonly'))
        results: list[ExecResult] = []
        # SQLite 字符串字面量无反斜杠转义('' 才是转义),关掉拆分器的 MySQL 转义语义
        for single in split_sql(stmt, backslash_escapes=False):
            ensure_writable(single, readonly)
            t0 = time.monotonic()
            try:
                cur = await self.conn.execute(single)
                async with cur:
                    if cur.description:
                        cols = [{'name': d[0], 'type': ''} for d in cur.description]
                        fetched = list(await cur.fetchmany(limit + 1))  # 返回类型是 Iterable,切片前先转 list
                        results.append(ExecResult(
                            kind='rows', columns=cols,
                            rows=[[jsonable(v) for v in r] for r in fetched[:limit]],
                            truncated=len(fetched) > limit,
                            elapsed_ms=int((time.monotonic() - t0) * 1000)))
                    else:
                        results.append(ExecResult(
                            kind='affected', affected=max(cur.rowcount, 0),
                            elapsed_ms=int((time.monotonic() - t0) * 1000)))
                # DML ... RETURNING 既有结果集也有写入:只看 description 会漏掉提交,
                # 连接关闭即回滚、写入静默丢失。统一按"连接仍处于事务中"判断提交;
                # 且必须等游标关闭(async with 退出)后再 commit —— 截断未抽干的
                # 语句仍 in-progress,直接 commit 会报 statements in progress。
                if self.conn.in_transaction:
                    await self.conn.commit()
            except QueryError:
                raise
            except Exception as e:  # sqlite3.OperationalError 等
                results.append(ExecResult(kind='error', error=str(e),
                                          elapsed_ms=int((time.monotonic() - t0) * 1000)))
        return results

    async def cancel(self) -> None:
        if self.conn is not None:
            await self.conn.interrupt()  # aiosqlite 的 interrupt 是协程,不 await 不会生效
