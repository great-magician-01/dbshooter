"""MySQL 驱动:aiomysql 连接池,information_schema 元数据。"""
from __future__ import annotations

import time
from typing import Any

import aiomysql

from .base import DriverBase, ExecResult, MetaNode, QueryError, ensure_writable, register
from .sqlutil import jsonable, split_sql

# information_schema.COLUMNS.DATA_TYPE 直接可用,执行结果的 type_code → 名称简化映射
_TYPE_NAMES = {0: 'decimal', 1: 'tinyint', 2: 'smallint', 3: 'int', 4: 'float', 5: 'double',
               7: 'timestamp', 8: 'bigint', 9: 'mediumint', 10: 'date', 11: 'time',
               12: 'datetime', 13: 'year', 15: 'varchar', 16: 'bit', 245: 'json',
               246: 'decimal', 249: 'tinyblob', 250: 'mediumblob', 251: 'blob',
               252: 'text', 253: 'varchar', 254: 'char'}

# 元数据枚举时排除的 MySQL 系统库
_SYSTEM_DBS = ('information_schema', 'mysql', 'performance_schema', 'sys')


@register
class MysqlDriver(DriverBase):
    kind = 'mysql'
    editor_mode = 'sql'

    def __init__(self, cfg: dict[str, Any]):
        super().__init__(cfg)
        self.pool: aiomysql.Pool | None = None

    async def connect(self) -> None:
        if self.pool is None:
            kwargs: dict[str, Any] = dict(
                host=self.cfg.get('host') or '127.0.0.1',
                port=int(self.cfg.get('port') or 3306),
                user=self.cfg.get('username') or 'root',
                password=self.cfg.get('password') or '',
                db=self.cfg.get('database') or None,
                autocommit=True, minsize=1, maxsize=5, connect_timeout=5)
            if bool(self.cfg.get('readonly')):
                # 会话级只读:EXPLAIN ANALYZE <DML>(MySQL 8.0.18+ 会真实执行语句)也拦截
                kwargs['init_command'] = 'SET SESSION TRANSACTION READ ONLY'
            self.pool = await aiomysql.create_pool(**kwargs)

    async def test(self) -> tuple[bool, str]:
        t0 = time.monotonic()
        await self.connect()
        assert self.pool is not None  # connect() 保证已建立
        async with self.pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute('SELECT VERSION()')
            (ver,) = await cur.fetchone()
        return True, f'MySQL {ver} · {int((time.monotonic()-t0)*1000)} ms'

    async def close(self) -> None:
        if self.pool is not None:
            self.pool.close()
            await self.pool.wait_closed()
            self.pool = None

    async def metadata(self, path: str) -> list[MetaNode]:
        await self.connect()
        assert self.pool is not None
        parts = [p for p in path.split('.') if p]
        async with self.pool.acquire() as conn, conn.cursor() as cur:
            if not parts:
                await cur.execute('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA'
                                  ' ORDER BY SCHEMA_NAME')
                return [MetaNode(path=r[0], label=r[0], kind='database', has_children=True)
                        for r in await cur.fetchall()]
            if len(parts) == 1:
                await cur.execute(
                    'SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES'
                    ' WHERE TABLE_SCHEMA=%s ORDER BY TABLE_TYPE, TABLE_NAME', (parts[0],))
                return [MetaNode(path=f'{parts[0]}.{n}', label=n,
                                 kind='view' if 'VIEW' in t else 'table', has_children=True)
                        for n, t in await cur.fetchall()]
            db, table = parts[0], parts[1]
            await cur.execute(
                'SELECT COLUMN_NAME, DATA_TYPE, COLUMN_KEY FROM information_schema.COLUMNS'
                ' WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION',
                (db, table))
            return [MetaNode(path=f'{path}.{n}', label=n, kind='column',
                             extra={'type': dt, 'pk': key == 'PRI'})
                    for n, dt, key in await cur.fetchall()]

    async def ai_namespaces(self) -> list[str]:
        # 绑定了库的连接只看当前库;未绑定则列全部非系统库
        bound = self.cfg.get('database')
        if bound:
            return [bound]
        dbs = await self.metadata('')
        return [n.label for n in dbs if n.label not in _SYSTEM_DBS]

    async def ai_tables(self, namespace: str | None = None) -> list[MetaNode]:
        ns = namespace or self.cfg.get('database') or ''
        return await self.metadata(ns) if ns else []

    async def ddl(self, tables: list[str]) -> str:
        await self.connect()
        assert self.pool is not None
        out = []
        async with self.pool.acquire() as conn, conn.cursor() as cur:
            for t in tables:
                try:
                    # 标识符反引号翻倍防注入;NO_BACKSLASH_ESCAPES 关闭时反斜杠在
                    # 反引号内仍是转义符,无法安全内嵌,直接拒绝含 \ 的名字
                    if '\\' in t:
                        continue
                    ident = '.'.join('`' + seg.replace('`', '``') + '`'
                                     for seg in t.split('.'))
                    await cur.execute(f'SHOW CREATE TABLE {ident}')
                    row = await cur.fetchone()
                    if row:
                        out.append(row[1] + ';')
                except Exception:
                    continue
        return '\n\n'.join(out)

    async def execute(self, stmt: str, limit: int = 500,
                      schema: str | None = None) -> list[ExecResult]:
        # schema 绑定仅 PG 支持:MySQL 的 USE 会污染连接池中被共享的连接
        await self.connect()
        assert self.pool is not None
        readonly = bool(self.cfg.get('readonly'))
        results: list[ExecResult] = []
        async with self.pool.acquire() as conn:
            for single in split_sql(stmt):
                ensure_writable(single, readonly)
                t0 = time.monotonic()
                try:
                    # SSCursor 流式游标:默认 Cursor 是 buffered,execute 即把全量结果
                    # 读进内存,limit/BUFFER_CAP 形同虚设,大表 SELECT 直接 OOM。
                    # 每条语句一个游标:关闭时丢弃未读行,避免残留结果集卡住下一条。
                    async with conn.cursor(aiomysql.SSCursor) as cur:
                        await cur.execute(single)
                        if cur.description:
                            cols = [{'name': d[0], 'type': _TYPE_NAMES.get(d[1], str(d[1]))}
                                    for d in cur.description]
                            fetched = await cur.fetchmany(limit + 1)
                            results.append(ExecResult(
                                kind='rows', columns=cols,
                                rows=[[jsonable(v) for v in r] for r in fetched[:limit]],
                                truncated=len(fetched) > limit,
                                elapsed_ms=int((time.monotonic() - t0) * 1000)))
                        else:
                            results.append(ExecResult(
                                kind='affected', affected=max(cur.rowcount, 0),
                                elapsed_ms=int((time.monotonic() - t0) * 1000)))
                except QueryError:
                    raise
                except Exception as e:
                    results.append(ExecResult(kind='error', error=str(e),
                                              elapsed_ms=int((time.monotonic() - t0) * 1000)))
        return results
