"""PostgreSQL 驱动:asyncpg 连接池,information_schema/pg_catalog 元数据。"""
from __future__ import annotations

import time
from typing import Any, cast

import asyncpg

from .base import (ColumnInfo, DriverBase, ExecResult, MetaNode, QueryError,
                   RelationInfo, ensure_writable, register)
from .sqlutil import jsonable, split_sql

# 常见类型 OID → 名称
_OID_NAMES = {16: 'bool', 17: 'bytea', 20: 'int8', 21: 'int2', 23: 'int4', 25: 'text',
              114: 'json', 700: 'float4', 701: 'float8', 1042: 'bpchar', 1043: 'varchar',
              1082: 'date', 1083: 'time', 1114: 'timestamp', 1184: 'timestamptz',
              1700: 'numeric', 2950: 'uuid', 3802: 'jsonb'}


def _qi(name: str) -> str:
    """标识符加双引号(内部引号翻倍),用于 SET search_path 等。"""
    return '"' + name.replace('"', '""') + '"'


@register
class PgDriver(DriverBase):
    kind = 'pg'
    editor_mode = 'sql'

    def __init__(self, cfg: dict[str, Any]):
        super().__init__(cfg)
        self.pool: asyncpg.Pool | None = None

    @property
    def current_db(self) -> str:
        return self.cfg.get('database') or 'postgres'

    async def connect(self) -> None:
        if self.pool is None:
            kwargs: dict[str, Any] = dict(
                host=self.cfg.get('host') or '127.0.0.1',
                port=int(self.cfg.get('port') or 5432),
                user=self.cfg.get('username') or 'postgres',
                password=self.cfg.get('password') or '',
                database=self.current_db, min_size=1, max_size=5, timeout=5)
            if bool(self.cfg.get('readonly')):
                # 会话级只读:连 WITH x AS (DELETE ...) 的数据修改 CTE 和
                # EXPLAIN ANALYZE <DML>(会真实执行语句)也一并拦截
                kwargs['server_settings'] = {'default_transaction_read_only': 'on'}
            self.pool = await asyncpg.create_pool(**kwargs)

    async def test(self) -> tuple[bool, str]:
        t0 = time.monotonic()
        await self.connect()
        assert self.pool is not None  # connect() 保证已建立
        async with self.pool.acquire() as conn:
            ver = await conn.fetchval('SHOW server_version')
        return True, f'PostgreSQL {ver} · {int((time.monotonic()-t0)*1000)} ms'

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def metadata(self, path: str) -> list[MetaNode]:
        await self.connect()
        assert self.pool is not None
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

    async def ai_namespaces(self) -> list[str]:
        return [n.label for n in await self.metadata(self.current_db)]

    async def ai_tables(self, namespace: str | None = None) -> list[MetaNode]:
        # metadata 路径需要 db.schema 两段,AI 侧只感知 schema 名
        return await self.metadata(f'{self.current_db}.{namespace or "public"}')

    async def ddl(self, tables: list[str]) -> str:
        """PG 无 SHOW CREATE,按 table_columns()/table_relations() 合成 DDL
        (含 NOT NULL/DEFAULT/主键生成列/identity/PRIMARY KEY/FOREIGN KEY,
        供 AI 上下文与 DDL 页签);视图/物化视图走 pg_get_viewdef 输出真实定义。"""
        await self.connect()
        assert self.pool is not None
        out = []
        for t in tables:
            parts = t.split('.')
            schema, table = (parts[-2], parts[-1]) if len(parts) >= 2 else ('public', parts[-1])
            async with self.pool.acquire() as conn:
                relkind = await conn.fetchval(
                    'SELECT c.relkind FROM pg_class c'
                    ' JOIN pg_namespace n ON n.oid = c.relnamespace'
                    ' WHERE n.nspname=$1 AND c.relname=$2', schema, table)
                if relkind is None:
                    continue  # 对象不存在
                if relkind in ('v', 'm'):
                    # format('%I.%I') 负责标识符引号,避免手工拼接注入
                    vdef = await conn.fetchval(
                        "SELECT pg_get_viewdef(format('%I.%I', $1, $2)::regclass, true)",
                        schema, table)
                    if vdef:
                        kind = 'MATERIALIZED VIEW' if relkind == 'm' else 'VIEW'
                        out.append(f'CREATE {kind} {schema}.{table} AS\n{vdef}')
                    continue
            if relkind not in ('r', 'p', 'f'):
                continue  # sequence / composite type / index 等:不合成 CREATE TABLE
            try:
                cols = await self.table_columns(t)
            except QueryError:
                continue
            if not cols:
                continue
            lines = []
            for c in cols:
                line = f'  "{c.name}" {c.type}'
                # identity / 存储生成列不能写成 DEFAULT(前者丢特性、后者重跑报错:
                # DEFAULT 表达式里不允许引用其它列)
                if c.identity in ('a', 'd'):
                    line += (' GENERATED '
                             + ('ALWAYS' if c.identity == 'a' else 'BY DEFAULT')
                             + ' AS IDENTITY')
                elif c.generated == 's' and c.default is not None:
                    line += f' GENERATED ALWAYS AS ({c.default}) STORED'
                elif c.default is not None:
                    line += f' DEFAULT {c.default}'
                if not c.nullable:
                    line += ' NOT NULL'
                lines.append(line)
            # pk 即主键内序号,排序后输出索引列序(而非列定义顺序)
            pk_cols = sorted((c for c in cols if c.pk), key=lambda c: c.pk)
            if pk_cols:
                lines.append('  PRIMARY KEY ('
                             + ', '.join(f'"{c.name}"' for c in pk_cols) + ')')
            # 出站外键(复合 FK 按约束名分组、seq 排序后拼列对)
            by_name: dict[str, list[RelationInfo]] = {}
            for r in await self.table_relations(t):
                if r.direction == 'out':
                    by_name.setdefault(r.name, []).append(r)
            for name, rs in by_name.items():
                rs.sort(key=lambda r: r.seq)
                src = ', '.join(f'"{r.column}"' for r in rs)
                ref = rs[0]
                dst = ', '.join(f'"{r.ref_column}"' for r in rs)
                lines.append(f'  CONSTRAINT "{name}" FOREIGN KEY ({src})'
                             f' REFERENCES {ref.ref_schema}.{ref.ref_table} ({dst})')
            out.append(f'CREATE TABLE {schema}.{table} (\n' + ',\n'.join(lines) + '\n);')
        return '\n\n'.join(out)

    async def table_columns(self, path: str) -> list[ColumnInfo]:
        await self.connect()
        assert self.pool is not None
        parts = path.split('.')
        schema, table = (parts[-2], parts[-1]) if len(parts) >= 2 else ('public', parts[-1])
        async with self.pool.acquire() as conn:
            # pg_attribute + format_type:varchar(64) 全型(psql \d 同款);
            # relkind 过滤挡住 sequence/composite type(AI 工具可传裸名,否则会
            # 把序列的 last_value 等属性当成表列)
            rows = await conn.fetch(
                'SELECT a.attname AS name,'
                ' pg_catalog.format_type(a.atttypid, a.atttypmod) AS type,'
                ' NOT a.attnotnull AS nullable,'
                ' pg_get_expr(d.adbin, d.adrelid) AS "default",'
                ' a.attnum AS ordinal,'
                ' col_description(a.attrelid, a.attnum) AS comment,'
                ' a.attgenerated AS generated,'
                ' a.attidentity AS identity'
                ' FROM pg_attribute a'
                ' JOIN pg_class c ON c.oid = a.attrelid'
                ' JOIN pg_namespace n ON n.oid = c.relnamespace'
                ' LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum'
                ' WHERE n.nspname=$1 AND c.relname=$2'
                ' AND a.attnum > 0 AND NOT a.attisdropped'
                " AND c.relkind IN ('r','p','v','m','f')"
                ' ORDER BY a.attnum', schema, table)
            if not rows:
                raise QueryError(f'表不存在: {schema}.{table}')
            # PK 列序按索引内顺序(array_position),不是列定义顺序:
            # PRIMARY KEY (b, a) 必须原样输出,否则合成 DDL 与真实结构不符
            pk_rows = await conn.fetch(
                'SELECT a.attname FROM pg_index i'
                ' JOIN pg_class c ON c.oid = i.indrelid'
                ' JOIN pg_namespace n ON n.oid = c.relnamespace'
                ' JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)'
                ' WHERE i.indisprimary AND n.nspname=$1 AND c.relname=$2'
                ' ORDER BY array_position(i.indkey, a.attnum)', schema, table)
        pk_pos = {r['attname']: i + 1 for i, r in enumerate(pk_rows)}
        return [ColumnInfo(name=r['name'], type=r['type'], nullable=r['nullable'],
                           default=r['default'], pk=pk_pos.get(r['name'], 0),
                           ordinal=r['ordinal'], comment=r['comment'] or '',
                           generated=r['generated'] or '',
                           identity=r['identity'] or '')
                for r in rows]

    async def table_relations(self, path: str) -> list[RelationInfo]:
        await self.connect()
        assert self.pool is not None
        parts = path.split('.')
        schema, table = (parts[-2], parts[-1]) if len(parts) >= 2 else ('public', parts[-1])
        async with self.pool.acquire() as conn:
            # unnest(conkey, confkey) WITH ORDINALITY:复合 FK 按位置配对列;
            # 双向过滤(我引用的 + 引用我的),跨 schema FK 由两侧 nspname 承载
            rows = await conn.fetch(
                'SELECT con.conname AS name,'
                ' src_ns.nspname AS src_schema, src.relname AS src_table,'
                ' sa.attname AS src_column,'
                ' dst_ns.nspname AS ref_schema, dst.relname AS ref_table,'
                ' da.attname AS ref_column, ord.n AS seq'
                ' FROM pg_constraint con'
                ' JOIN pg_class src ON src.oid = con.conrelid'
                ' JOIN pg_namespace src_ns ON src_ns.oid = src.relnamespace'
                ' JOIN pg_class dst ON dst.oid = con.confrelid'
                ' JOIN pg_namespace dst_ns ON dst_ns.oid = dst.relnamespace'
                ' JOIN unnest(con.conkey, con.confkey) WITH ORDINALITY AS ord(ck, dk, n)'
                '  ON TRUE'
                ' JOIN pg_attribute sa ON sa.attrelid = con.conrelid AND sa.attnum = ord.ck'
                ' JOIN pg_attribute da ON da.attrelid = con.confrelid AND da.attnum = ord.dk'
                " WHERE con.contype = 'f'"
                # conparentid=0:分区表的 FK 会被 PG 克隆到每个分区,不带这个过滤
                # 会让被引表按分区数收到重复的入站关系
                ' AND con.conparentid = 0'
                ' AND ((src_ns.nspname=$1 AND src.relname=$2)'
                '  OR (dst_ns.nspname=$1 AND dst.relname=$2))'
                ' ORDER BY con.conname, ord.n', schema, table)
        rels: list[RelationInfo] = []
        for r in rows:
            # 自引用行同时命中两侧条件,先判 'out',天然只记一次
            direction = ('out' if r['src_schema'] == schema and r['src_table'] == table
                         else 'in')
            rels.append(RelationInfo(
                name=r['name'], direction=direction,
                schema=r['src_schema'], table=r['src_table'], column=r['src_column'],
                ref_schema=r['ref_schema'], ref_table=r['ref_table'],
                ref_column=r['ref_column'], seq=r['seq'] - 1))  # PG 序号 1 起,归一为 0 起
        return rels

    async def execute(self, stmt: str, limit: int = 500,
                      schema: str | None = None) -> list[ExecResult]:
        await self.connect()
        assert self.pool is not None
        readonly = bool(self.cfg.get('readonly'))
        results: list[ExecResult] = []
        async with self.pool.acquire() as acq:
            # asyncpg 的 acquire() 无类型标注且返回动态代理,按 Connection 使用
            conn = cast(asyncpg.Connection, acq)
            # PG 默认 standard_conforming_strings=on,字符串里的 \ 是字面量而非转义
            for single in split_sql(stmt, backslash_escapes=False):
                ensure_writable(single, readonly)
                t0 = time.monotonic()
                try:
                    if schema:
                        # 页签绑定 schema:单条语句包一层事务,SET LOCAL 的 search_path
                        # 仅本事务内生效、提交即失效,不污染池中被其他页签共用的连接
                        async with conn.transaction():
                            await conn.execute(
                                f'SET LOCAL search_path TO {_qi(schema)}')
                            results.append(
                                await self._exec_one(conn, single, limit, t0))
                    else:
                        results.append(await self._exec_one(conn, single, limit, t0))
                except QueryError:
                    raise
                except Exception as e:
                    results.append(ExecResult(kind='error', error=str(e),
                                              elapsed_ms=int((time.monotonic() - t0) * 1000)))
        return results

    async def _exec_one(self, conn: asyncpg.Connection, single: str,
                        limit: int, t0: float) -> ExecResult:
        """执行单条语句:有结果集走游标(读 limit+1 判截断),否则记影响行数。"""
        ps = await conn.prepare(single)
        attrs = ps.get_attributes()
        if attrs:  # 有结果集
            cols = [{'name': a.name, 'type': _OID_NAMES.get(a.type.oid, str(a.type.oid))}
                    for a in attrs]
            # PreparedStatement.fetch(*args) 的位置参数是查询参数而非行数;
            # 改用游标读取 limit+1 行判定截断(游标只能在事务内创建;外层已有
            # schema 事务时这里嵌套为 savepoint,语义不变)
            async with conn.transaction():
                cur = await ps.cursor()
                recs = await cur.fetch(limit + 1)
            return ExecResult(
                kind='rows', columns=cols,
                rows=[[jsonable(r[a.name]) for a in attrs] for r in recs[:limit]],
                truncated=len(recs) > limit,
                elapsed_ms=int((time.monotonic() - t0) * 1000))
        status = await conn.execute(single)
        affected = int(status.split()[-1]) if status.split()[-1].isdigit() else 0
        return ExecResult(kind='affected', affected=affected,
                          elapsed_ms=int((time.monotonic() - t0) * 1000))
