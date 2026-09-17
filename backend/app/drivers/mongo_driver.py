"""MongoDB 驱动:motor 异步客户端,JSON 查询语法(db.coll.find({...}).sort().limit())。"""
from __future__ import annotations

import re
import time
from typing import Any

import pyjson5
from bson import ObjectId, json_util
from motor.motor_asyncio import AsyncIOMotorClient

from .base import DriverBase, ExecResult, MetaNode, QueryError, register

# db.coll.method(args) 后跟 .sort()/limit()/skip() 链
_HEAD_RE = re.compile(r'^\s*db\.([\w$]+)\.(\w+)\s*\(')
_CHAIN_RE = re.compile(r'\.(\w+)\s*\(([^)]*)\)')
_CHAIN_FULL = re.compile(r'(\s*\.\w+\s*\([^)]*\))*\s*;?\s*$')
SUPPORTED = {'find', 'countDocuments', 'aggregate', 'insertOne', 'deleteOne', 'deleteMany',
             'updateOne', 'updateMany'}


def _balanced_args(stmt: str, start: int) -> tuple[str, str]:
    """从开括号后第一个字符开始,按引号/括号配对提取参数串;返回 (args, 剩余链)。"""
    depth, i, quote = 1, start, None
    while i < len(stmt) and depth:
        ch = stmt[i]
        if quote:
            if ch == '\\':
                i += 1
            elif ch == quote:
                quote = None
        elif ch in ('\'', '"'):
            quote = ch
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        i += 1
    if depth:
        raise QueryError('括号不配对,请检查查询语法')
    return stmt[start:i - 1], stmt[i:]


def _preprocess(args: str) -> str:
    """把 shell 风格字面量转成可解析 JSON5:ISODate('..')/ObjectId('..')。"""
    args = re.sub(r"ISODate\(\s*'([^']*)'\s*\)", r'"$$date:\1"', args)
    args = re.sub(r"ObjectId\(\s*'([^']*)'\s*\)", r'"$$oid:\1"', args)
    return args


def _restore(value: Any) -> Any:
    """解析后还原占位符为 datetime/ObjectId。"""
    if isinstance(value, str):
        if value.startswith('$$date:'):
            from datetime import datetime, timezone
            return datetime.fromisoformat(value[7:].replace('Z', '+00:00')).astimezone(timezone.utc)
        if value.startswith('$$oid:'):
            return ObjectId(value[6:])
        return value
    if isinstance(value, list):
        return [_restore(v) for v in value]
    if isinstance(value, dict):
        return {k: _restore(v) for k, v in value.items()}
    return value


def parse_args(text: str) -> list[Any]:
    """解析方法参数(json5,宽容未加引号的 key 与单引号)。"""
    text = text.strip()
    if not text:
        return []
    value = _restore(pyjson5.decode(f'[{_preprocess(text)}]'))
    return value


def parse_shell(stmt: str) -> dict[str, Any]:
    """解析 db.coll.find({...}).sort({...}).limit(50) 形式;失败抛 QueryError。"""
    m = _HEAD_RE.match(stmt)
    if not m:
        raise QueryError('无法解析查询,期望形如: db.<collection>.find({...}).sort({...}).limit(50)')
    coll, method = m.groups()
    if method not in SUPPORTED:
        raise QueryError(f'一期支持的方法: {", ".join(sorted(SUPPORTED))};收到: {method}')
    args_text, chain = _balanced_args(stmt, m.end())
    if not _CHAIN_FULL.match(chain):
        raise QueryError(f'无法解析的链式调用: {chain.strip()}')
    spec: dict[str, Any] = {'collection': coll, 'method': method,
                            'args': parse_args(args_text),
                            'sort': None, 'limit': 500, 'skip': 0}
    for name, arg in _CHAIN_RE.findall(chain or ''):
        if name == 'sort':
            parsed = parse_args(arg)
            spec['sort'] = list(parsed[0].items()) if parsed else None
        elif name in ('limit', 'skip'):
            parsed = parse_args(arg)
            spec[name] = int(parsed[0]) if parsed else spec[name]
    return spec


def doc_to_jsonable(doc: dict[str, Any]) -> dict[str, Any]:
    """bson 类型 → JSON 可序列化(ObjectId/datetime 等)。"""
    return pyjson5.decode(json_util.dumps(doc))


@register
class MongoDriver(DriverBase):
    kind = 'mongo'
    editor_mode = 'json-query'

    def __init__(self, cfg: dict[str, Any]):
        super().__init__(cfg)
        self.client: AsyncIOMotorClient | None = None

    async def connect(self) -> None:
        if self.client is None:
            params = self.cfg.get('params') or {}
            if params.get('uri'):
                client = AsyncIOMotorClient(params['uri'], serverSelectionTimeoutMS=5000)
            else:
                client = AsyncIOMotorClient(
                    host=self.cfg.get('host') or '127.0.0.1',
                    port=int(self.cfg.get('port') or 27017),
                    username=self.cfg.get('username') or None,
                    password=self.cfg.get('password') or None,
                    serverSelectionTimeoutMS=5000)
            await client.admin.command('ping')  # 连不上即抛错,不缓存半成品
            self.client = client

    async def test(self) -> tuple[bool, str]:
        t0 = time.monotonic()
        await self.connect()
        assert self.client is not None  # connect() 保证已建立
        info = await self.client.server_info()
        return True, f"MongoDB {info.get('version','?')} · {int((time.monotonic()-t0)*1000)} ms"

    async def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

    def _db(self, name: str | None = None):
        assert self.client is not None  # 调用前需 await connect()
        return self.client[name or self.cfg.get('database') or 'test']

    async def metadata(self, path: str) -> list[MetaNode]:
        await self.connect()
        assert self.client is not None
        parts = [p for p in path.split('.') if p]
        if not parts:
            names = await self.client.list_database_names()
            return [MetaNode(path=n, label=n, kind='database', has_children=True)
                    for n in names if n not in ('admin', 'local', 'config')]
        if len(parts) == 1:
            names = await self._db(parts[0]).list_collection_names()
            return [MetaNode(path=f'{parts[0]}.{c}', label=c, kind='collection',
                             has_children=True) for c in sorted(names)]
        coll = self._db(parts[0])[parts[1]]
        nodes = []
        for idx in await coll.index_information():
            nodes.append(MetaNode(path=f'{path}.{idx}', label=idx, kind='index'))
        return nodes

    async def execute(self, stmt: str, limit: int = 500,
                      schema: str | None = None) -> list[ExecResult]:
        # NoSQL 无 schema 会话概念,忽略
        await self.connect()
        spec = parse_shell(stmt)
        if self.cfg.get('readonly') and spec['method'] not in ('find', 'countDocuments'):
            raise QueryError(f"当前连接为只读模式,已拦截写操作: {spec['method']}")
        coll = self._db()[spec['collection']]
        t0 = time.monotonic()
        try:
            method = spec['method']
            args = spec['args']
            if method == 'find':
                cursor = coll.find(*args)
                if spec['sort']:
                    cursor = cursor.sort(spec['sort'])
                if spec['skip']:
                    cursor = cursor.skip(spec['skip'])
                cursor = cursor.limit(min(spec['limit'] or limit, limit) + 1)
                docs = [doc_to_jsonable(d) async for d in cursor]
                truncated = len(docs) > limit
                docs = docs[:limit]
                keys: list[str] = []
                for d in docs:
                    for k in d:
                        if k not in keys:
                            keys.append(k)
                return [ExecResult(
                    kind='documents', raw=docs,
                    columns=[{'name': k, 'type': ''} for k in keys],
                    rows=[[_cell(d.get(k)) for k in keys] for d in docs],
                    truncated=truncated, elapsed_ms=int((time.monotonic() - t0) * 1000))]
            if method == 'countDocuments':
                n = await coll.count_documents(args[0] if args else {})
                return [ExecResult(kind='command', raw=n,
                                   columns=[{'name': 'count', 'type': 'int'}], rows=[[n]],
                                   elapsed_ms=int((time.monotonic() - t0) * 1000))]
            if method == 'aggregate':
                raw_pipeline = args[0] if args else []
                if not isinstance(raw_pipeline, list):
                    raise QueryError('aggregate 第一个参数必须是管道数组 [{...}, ...]')
                pipeline = list(raw_pipeline)
                # 管道尾追加 $limit,服务端限量,避免大结果集全量拉进内存
                pipeline.append({'$limit': limit + 1})
                docs = [doc_to_jsonable(d) async for d in coll.aggregate(pipeline)]
                truncated = len(docs) > limit
                return [ExecResult(kind='documents', raw=docs[:limit],
                                   columns=[], rows=[],
                                   truncated=truncated,
                                   elapsed_ms=int((time.monotonic() - t0) * 1000))]
            # 写操作
            fn = getattr(coll, method)
            res = await fn(*args)
            affected = getattr(res, 'modified_count', None) \
                or getattr(res, 'deleted_count', None) \
                or (1 if getattr(res, 'inserted_id', None) else 0)
            return [ExecResult(kind='affected', affected=affected,
                               elapsed_ms=int((time.monotonic() - t0) * 1000))]
        except QueryError:
            raise
        except Exception as e:
            return [ExecResult(kind='error', error=str(e),
                               elapsed_ms=int((time.monotonic() - t0) * 1000))]


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json_util.dumps(v, ensure_ascii=False)
    return v
