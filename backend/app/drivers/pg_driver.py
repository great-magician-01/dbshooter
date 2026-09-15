"""PostgreSQL 驱动:asyncpg 连接池,information_schema/pg_catalog 元数据。"""
from __future__ import annotations

import time

import asyncpg

from .base import DriverBase, ExecResult, MetaNode, QueryError, ensure_writable, register
from .sqlutil import split_sql

# 常见类型 OID → 名称
_OID_NAMES = {16: 'bool', 17: 'bytea', 20: 'int8', 21: 'int2', 23: 'int4', 25: 'text',
              114: 'json', 700: 'float4', 701: 'float8', 1042: 'bpchar', 1043: 'varchar',
              1082: 'date', 1083: 'time', 1114: 'timestamp', 1184: 'timestamptz',
              1700: 'numeric', 2950: 'uuid', 3802: 'jsonb'}


@register
class PgDriver(DriverBase):
    kind = 'pg'
    editor_mode = 'sql'

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.pool = None

    @property
    def current_db(self) -> str:
        return self.cfg.get('database') or 'postgres'

    async def connect(self) -> None:
        if self.pool is None:
            self.pool = await asyncpg.create_pool(
                host=self.cfg.get('host') or '127.0.0.1',
                port=int(self.cfg.get('port') or 5432),
                user=self.cfg.get('username') or 'postgres',
                password=self.cfg.get('password') or '',
                database=self.current_db, min_size=1, max_size=5, timeout=5)

    async def test(self) -> tuple[bool, str]:
        t0 = time.monotonic()
        await self.connect()
        async with self.pool.acquire() as conn:
            ver = await conn.fetchval('SHOW server_version')
        return True, f'PostgreSQL {ver} · {int((time.monotonic()-t0)*1000)} ms'

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def metadata(self, path: str) -> list[MetaNode]:
        await self.connect()
        parts = [p for p in path.split('.') if p]
        async with self.pool.acquire() as conn:
            if not parts:
                rows = await conn.fetch('SELECT datname FROM pg_database'
                                        ' WHERE NOT datistemplate ORDER BY datname')
                # 一期:仅当前库可展开(跨库需重连)
                return [MetaNode(path=r['datname'], label=r['datname'], kind='database',
                                 has_children=r['datname'] == self.current_db)
                        for r in rows]
            if len(parts) == 1:
                rows = await conn.fetch(
                    'SELECT schema_name FROM information_schema.schemata'
                    " WHERE schema_name NOT LIKE 'pg_%' AND schema_name <> 'information_schema'"
                    ' ORDER BY schema_name')
                return [MetaNode(path=f"{parts[0]}.{r['schema_name']}", label=r['schema_name'],
                                 kind='schema', has_children=True) for r in rows]
            if len(parts) == 2:
                rows = await conn.fetch(
                    'SELECT table_name, table_type FROM information_schema.tables'
                    ' WHERE table_schema=$1 ORDER BY table_type, table_name', parts[1])
                return [MetaNode(path=f"{path}.{r['table_name']}", label=r['table_name'],
                                 kind='view' if 'VIEW' in r['table_type'] else 'table',
                                 has_children=True) for r in rows]
            schema, table = parts[1], parts[2]
            rows = await conn.fetch(
                'SELECT column_name, data_type FROM information_schema.columns'
                ' WHERE table_schema=$1 AND table_name=$2 ORDER BY ordinal_position',
                schema, table)
            return [MetaNode(path=f"{path}.{r['column_name']}", label=r['column_name'],
                             kind='column', extra={'type': r['data_type']}) for r in rows]

    async def ddl(self, tables: list[str]) -> str:
        """PG 无 SHOW CREATE,按 information_schema 合成简化 DDL(供 AI 上下文)。"""
        await self.connect()
        out = []
        async with self.pool.acquire() as conn:
            for t in tables:
                parts = t.split('.')
                schema, table = (parts[-2], parts[-1]) if len(parts) >= 2 else ('public', parts[-1])
                rows = await conn.fetch(
                    'SELECT column_name, data_type, is_nullable FROM information_schema.columns'
                    ' WHERE table_schema=$1 AND table_name=$2 ORDER BY ordinal_position',
                    schema, table)
                if rows:
                    cols = ',\n  '.join(
                        f'"{r["column_name"]}" {r["data_type"]}'
                        + ('' if r['is_nullable'] == 'YES' else ' NOT NULL') for r in rows)
                    out.append(f'CREATE TABLE {schema}.{table} (\n  {cols}\n);')
        return '\n\n'.join(out)

    async def execute(self, stmt: str, limit: int = 500) -> list[ExecResult]:
        await self.connect()
        readonly = bool(self.cfg.get('readonly'))
        results: list[ExecResult] = []
        async with self.pool.acquire() as conn:
            for single in split_sql(stmt):
                ensure_writable(single, readonly)
                t0 = time.monotonic()
                try:
                    ps = await conn.prepare(single)
                    attrs = ps.get_attributes()
                    if attrs:  # 有结果集
                        cols = [{'name': a.name, 'type': _OID_NAMES.get(a.type.oid, str(a.type.oid))}
                                for a in attrs]
                        recs = await ps.fetch(limit + 1)
                        results.append(ExecResult(
                            kind='rows', columns=cols,
                            rows=[[r[a.name] for a in attrs] for r in recs[:limit]],
                            truncated=len(recs) > limit,
                            elapsed_ms=int((time.monotonic() - t0) * 1000)))
                    else:
                        status = await conn.execute(single)
                        affected = int(status.split()[-1]) if status.split()[-1].isdigit() else 0
                        results.append(ExecResult(
                            kind='affected', affected=affected,
                            elapsed_ms=int((time.monotonic() - t0) * 1000)))
                except QueryError:
                    raise
                except Exception as e:
                    results.append(ExecResult(kind='error', error=str(e),
                                              elapsed_ms=int((time.monotonic() - t0) * 1000)))
        return results
