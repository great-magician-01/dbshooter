"""Redis 驱动:命令行式执行 + SCAN 键空间浏览(禁止 KEYS *)。"""
from __future__ import annotations

import shlex
import time
from typing import Any

import redis.asyncio as aioredis

from .base import DriverBase, ExecResult, MetaNode, QueryError, register

SCAN_COUNT = 500          # 每次 SCAN 批量
MAX_GROUP_KEYS = 200      # 单个分组节点下直接展示的 key 上限

# 会无限期挂起连接的命令:查询在无读超时的 WS 会话里执行,一旦阻塞整个连接不可用
_BLOCKING_CMDS = {'BLPOP', 'BRPOP', 'BRPOPLPUSH', 'BLMOVE', 'BZPOPMIN', 'BZPOPMAX',
                  'SUBSCRIBE', 'PSUBSCRIBE', 'SSUBSCRIBE', 'MONITOR', 'WAIT'}


def format_command_result(value, indent: int = 0) -> list[str]:
    """redis 命令回包 → 可读文本行(纯函数,便于测试)。"""
    pad = '  ' * indent
    if value is None:
        return [f'{pad}(nil)']
    if isinstance(value, bytes):
        return [pad + value.decode('utf-8', 'replace')]
    if isinstance(value, (str, int, float)):
        return [pad + str(value)]
    if isinstance(value, (list, tuple, set)):
        if not value:
            return [f'{pad}(empty)']
        lines = []
        for i, v in enumerate(value if not isinstance(value, set) else sorted(value, key=str), 1):
            if isinstance(v, (list, tuple, dict, set)):
                lines.append(f'{pad}{i})')
                lines.extend(format_command_result(v, indent + 1))
            else:
                s = v.decode('utf-8', 'replace') if isinstance(v, bytes) else str(v)
                lines.append(f'{pad}{i}) {s}')
        return lines
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            k = k.decode('utf-8', 'replace') if isinstance(k, bytes) else k
            v = v.decode('utf-8', 'replace') if isinstance(v, bytes) else v
            lines.append(f'{pad}{k} => {v}')
        return lines or [f'{pad}(empty)']
    return [pad + repr(value)]


@register
class RedisDriver(DriverBase):
    kind = 'redis'
    editor_mode = 'command'

    def __init__(self, cfg: dict[str, Any]):
        super().__init__(cfg)
        self._clients: dict[int, aioredis.Redis] = {}

    def _client(self, db: int | None = None) -> aioredis.Redis:
        db = db if db is not None else int((self.cfg.get('params') or {}).get('db', 0))
        if db not in self._clients:
            self._clients[db] = aioredis.Redis(
                host=self.cfg.get('host') or '127.0.0.1',
                port=int(self.cfg.get('port') or 6379),
                password=self.cfg.get('password') or None,
                db=db, decode_responses=True, socket_connect_timeout=5)
        return self._clients[db]

    async def connect(self) -> None:
        await self._client().ping()

    async def test(self) -> tuple[bool, str]:
        t0 = time.monotonic()
        info = await self._client().info('server')
        return True, f"Redis {info.get('redis_version','?')} · {int((time.monotonic()-t0)*1000)} ms"

    async def close(self) -> None:
        for c in self._clients.values():
            await c.aclose()
        self._clients.clear()

    async def metadata(self, path: str) -> list[MetaNode]:
        parts = [p for p in path.split('/') if p]
        if not parts:
            db_count = 16
            try:
                cfg = await self._client().config_get('databases')
                db_count = int(cfg.get('databases') or 16)
            except Exception:
                pass  # 无 CONFIG 权限时按默认 16 个库
            return [MetaNode(path=f'db{i}', label=f'db{i}', kind='database', has_children=True)
                    for i in range(db_count)]
        db = int(parts[0][2:])
        prefix = parts[1] if len(parts) > 1 else ''
        return await self._scan_level(db, prefix)

    async def _scan_level(self, db: int, prefix: str) -> list[MetaNode]:
        """SCAN 游标扫描,按冒号下一段分组;返回分组节点 + 叶子键。"""
        groups: dict[str, int] = {}
        keys: list[str] = []
        cursor = 0
        scanned = 0
        r = self._client(db)
        while True:
            cursor, batch = await r.scan(cursor=cursor, match=f'{prefix}*', count=SCAN_COUNT)
            for k in batch:
                # 标注上 key 可能是 bytes(decode_responses 未进类型系统),运行时已是 str
                k = k.decode('utf-8', 'replace') if isinstance(k, bytes) else k
                scanned += 1
                rest = k[len(prefix):]
                seg, sep, _tail = rest.partition(':')
                if sep:
                    groups[seg + ':'] = groups.get(seg + ':', 0) + 1
                else:
                    keys.append(k)
            if cursor == 0 or scanned >= 5000:
                break
        base = f'db{db}/{prefix}'
        nodes = [MetaNode(path=f'{base}{g}', label=f'{g} ({n})', kind='keygroup',
                          has_children=True) for g, n in sorted(groups.items())]
        shown = sorted(keys)[:MAX_GROUP_KEYS]
        nodes += [MetaNode(path=f'{base}~{k}', label=k[len(prefix):], kind='key',
                           extra={'key': k}) for k in shown]
        if len(keys) > MAX_GROUP_KEYS:
            # 截断要有迹可循,不能让用户误以为只有这些键(has_children=False 的
            # 分组节点不可展开,仅作提示)
            nodes.append(MetaNode(path=f'{base}~', kind='keygroup', has_children=False,
                                  label=f'…另有 {len(keys) - len(shown)} 个键未列出'))
        return nodes

    async def key_detail(self, db: int, key: str) -> dict[str, Any]:
        """键详情视图:TYPE + PTTL + 按类型的值(限量截取)。"""
        r = self._client(db)
        ktype = await r.type(key)
        ttl = await r.pttl(key)
        value: object
        # 说明:hgetall/lrange/smembers 在 redis-py 8.1 的类型标注中 async overload 未生效,
        # pyright 会误判为同步返回值;运行时确为协程,故行级 ignore(升级 redis-py 后可移除)
        if ktype == 'string':
            value = await r.get(key)
        elif ktype == 'hash':
            value = await r.hgetall(key)  # pyright: ignore[reportGeneralTypeIssues]
        elif ktype == 'list':
            value = await r.lrange(key, 0, 199)  # pyright: ignore[reportGeneralTypeIssues]
        elif ktype == 'set':
            value = sorted(await r.smembers(key))[:200]  # pyright: ignore[reportGeneralTypeIssues]
        elif ktype == 'zset':
            value = await r.zrange(key, 0, 199, withscores=True)
        elif ktype == 'none':
            raise QueryError(f'键不存在: {key}')
        else:
            value = f'(类型 {ktype} 暂不支持值视图,可用命令行查询)'
        return {'key': key, 'type': ktype,
                'ttl_ms': ttl, 'value': value}

    async def execute(self, stmt: str, limit: int = 500,
                      schema: str | None = None) -> list[ExecResult]:
        # NoSQL 无 schema 会话概念,忽略
        stmt = stmt.strip()
        if not stmt:
            return []
        try:
            args = shlex.split(stmt)
        except ValueError as e:
            raise QueryError(f'命令解析失败: {e}') from e
        if not args:
            return []
        cmd = args[0].upper()
        if cmd == 'KEYS':
            raise QueryError('生产安全:禁用 KEYS *,请改用左侧键浏览器(SCAN)或 SCAN 命令')
        if cmd in _BLOCKING_CMDS:
            raise QueryError(f'已拦截阻塞命令 {cmd}:它会挂起整个查询会话,'
                             '请改用非阻塞变体(如 LPOP / ZPOPMIN)')
        if self.cfg.get('readonly') and cmd not in (
                'GET', 'MGET', 'HGET', 'HGETALL', 'HMGET', 'LRANGE', 'SMEMBERS', 'ZRANGE',
                'ZRANGEBYSCORE', 'TYPE', 'PTTL', 'TTL', 'EXISTS', 'STRLEN', 'HLEN', 'LLEN',
                'SCARD', 'ZCARD', 'SCAN', 'HSCAN', 'SSCAN', 'ZSCAN', 'INFO', 'PING',
                'ZREVRANGE', 'HKEYS', 'HVALS', 'ZSCORE', 'ZRANK'):
            raise QueryError(f'当前连接为只读模式,已拦截命令: {cmd}')
        t0 = time.monotonic()
        try:
            raw = await self._client().execute_command(*args)
        except Exception as e:
            return [ExecResult(kind='error', error=str(e),
                               elapsed_ms=int((time.monotonic() - t0) * 1000))]
        return [ExecResult(kind='command', raw=raw,
                           columns=[{'name': cmd, 'type': ''}],
                           rows=[[line] for line in format_command_result(raw)],
                           elapsed_ms=int((time.monotonic() - t0) * 1000))]
