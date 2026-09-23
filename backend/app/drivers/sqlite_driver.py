"""SQLite 驱动:零配置本地文件库,aiosqlite 异步执行。"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import aiosqlite

from .base import (ColumnInfo, DriverBase, ExecResult, MetaNode, QueryError,
                   RelationInfo, ensure_writable, register)
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

    async def table_columns(self, path: str) -> list[ColumnInfo]:
        await self.connect()
        assert self.conn is not None
        table = path.split('.')[-1]
        # PRAGMA 不支持参数绑定,标识符双引号翻倍防注入(同 metadata())
        safe = table.replace('"', '""')
        cols: list[ColumnInfo] = []
        async with self.conn.execute(f'PRAGMA table_info("{safe}")') as cur:
            async for cid, name, ctype, notnull, dflt, pk in cur:
                cols.append(ColumnInfo(name=name, type=ctype or '',
                                       nullable=not notnull,
                                       default=None if dflt is None else str(dflt),
                                       pk=pk, ordinal=cid))
        if not cols:
            raise QueryError(f'表不存在: {table}')
        return cols

    async def _pk_columns(self, table: str) -> list[str]:
        """表的有序主键列(foreign_key_list 省略被引列时 to 为 NULL 的回退)。

        复合主键被隐式引用(FOREIGN KEY(x,y) REFERENCES parents,未写被引列)时,
        PRAGMA foreign_key_list 每一行的 to 都是 NULL,必须按 seq 对应主键的
        第 seq 列 —— 只取首列会让复合 FK 的所有列都锚到同一列上。
        """
        assert self.conn is not None
        safe = table.replace('"', '""')
        async with self.conn.execute(f'PRAGMA table_info("{safe}")') as cur:
            # pk 列返回值是主键内序号(1 起),0=非主键列
            rows = [(pk, name) async for _cid, name, _ctype, _notnull, _dflt, pk in cur]
        return [name for _pk, name in sorted(r for r in rows if r[0])]

    async def _fk_ref_column(self, table: str, ref_table: str,
                             to_col: str | None, seq: int) -> str | None:
        """外键行的被引列:显式写了用显式值,省略时按 seq 取被引表主键列。"""
        if to_col is not None:
            return to_col
        pks = await self._pk_columns(ref_table)
        return pks[seq] if seq < len(pks) else None

    async def table_relations(self, path: str) -> list[RelationInfo]:
        await self.connect()
        conn = self.conn
        assert conn is not None  # 闭包内收窄不传递,捕获局部变量供 fk_rows 使用
        table = path.split('.')[-1]
        relations: list[RelationInfo] = []

        async def fk_rows(src: str) -> list[tuple[Any, ...]]:
            # (id, seq, table, from, to, on_update, on_delete, match)
            safe = src.replace('"', '""')
            async with conn.execute(f'PRAGMA foreign_key_list("{safe}")') as cur:
                return [tuple(r) async for r in cur]

        # 出站:本表引用别人(自引用也在这里记一次,'out')
        for fid, seq, ref_table, from_col, to_col, *_rest in await fk_rows(table):
            ref_col = await self._fk_ref_column(table, ref_table, to_col, seq)
            if ref_col is None:
                continue  # 被引表无主键且省略被引列,无法定位锚点列
            relations.append(RelationInfo(
                name=f'fk_{table}_{fid}', direction='out', schema='main',
                table=table, column=from_col,
                ref_schema='main', ref_table=ref_table, ref_column=ref_col, seq=seq))

        # 入站:别人引用本表(源表==本表的自引用上面已记,跳过)
        async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name NOT LIKE 'sqlite_%'"
                # 预筛:只有建表 SQL 里出现 REFERENCES 的表才可能有入站 FK,
                # 否则每张表都要一次 PRAGMA(千表库实测 250ms)
                " AND sql LIKE '%REFERENCES%'") as cur:
            others = [r[0] async for r in cur]
        for src in others:
            if src == table:
                continue
            for fid, seq, ref_table, from_col, to_col, *_rest in await fk_rows(src):
                if ref_table != table:
                    continue
                ref_col = await self._fk_ref_column(src, table, to_col, seq)
                if ref_col is None:
                    continue
                relations.append(RelationInfo(
                    name=f'fk_{src}_{fid}', direction='in', schema='main',
                    table=src, column=from_col,
                    ref_schema='main', ref_table=table, ref_column=ref_col, seq=seq))
        return relations

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
