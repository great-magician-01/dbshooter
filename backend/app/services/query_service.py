"""查询会话服务:执行 → 内存缓冲 → 分页拉取 / 取消 / 历史落库。

大结果集保护:驱动层最多取 BUFFER_CAP+1 行,超出标记 truncated,
前端分页仅从内存缓冲切片,不会把浏览器打爆。
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .. import db
from ..drivers import ExecResult, QueryError
from .connection_manager import manager

BUFFER_CAP = 2000          # 内存缓冲行数上限
TTL_SECONDS = 600          # 结果缓冲保留时长

Emit = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class QueryCtx:
    query_id: str
    conn_id: str
    stmt: str
    status: str = 'running'          # running | done | error | cancelled
    results: list[ExecResult] = field(default_factory=list)
    error: str | None = None
    task: asyncio.Task | None = None

    @property
    def first_rows(self) -> ExecResult | None:
        return next((r for r in self.results if r.kind in ('rows', 'documents')), None)


class QueryService:
    def __init__(self) -> None:
        self._queries: dict[str, QueryCtx] = {}

    def get(self, query_id: str) -> QueryCtx:
        ctx = self._queries.get(query_id)
        if not ctx:
            raise QueryError(f'查询不存在或已过期: {query_id}')
        return ctx

    async def execute(self, conn_id: str, stmt: str, emit: Emit,
                     schema: str | None = None) -> str:
        qid = uuid.uuid4().hex[:12]
        ctx = QueryCtx(query_id=qid, conn_id=conn_id, stmt=stmt)
        self._queries[qid] = ctx

        async def run() -> None:
            # emit 只负责通知,客户端断开不该带走历史落库;也不允许异常逃出任务
            # (任务异常无人取回会刷 "Task exception was never retrieved")
            async def safe_emit(ev: dict[str, Any]) -> None:
                try:
                    await emit(ev)
                except Exception:
                    pass

            try:
                driver = await manager.get(conn_id)
                await safe_emit({'event': 'query.started', 'data': {'query_id': qid}})
                results = await driver.execute(stmt, limit=BUFFER_CAP, schema=schema)
                ctx.results = results
                err = next((r.error for r in results if r.error), None)
                ctx.status = 'error' if err else 'done'
                first = ctx.first_rows
                if first:
                    await safe_emit({'event': 'query.rows', 'data': {
                        'query_id': qid, 'columns': first.columns,
                        'rows': first.rows[:200], 'has_more': len(first.rows) > 200,
                        'truncated': first.truncated}})
                elapsed = sum(r.elapsed_ms for r in results)
                summary = [{'kind': r.kind, 'affected': r.affected, 'error': r.error}
                           for r in results]
                await safe_emit({'event': 'query.done' if not err else 'query.error', 'data': {
                    'query_id': qid, 'elapsed_ms': elapsed,
                    'row_count': len(first.rows) if first else 0,
                    'summary': summary, 'error': err}})
                db.add_history(conn_id, stmt, elapsed,
                               len(first.rows) if first else 0, ctx.status)
            except QueryError as e:
                ctx.status = 'error'
                ctx.error = str(e)
                await safe_emit({'event': 'query.error', 'data': {'query_id': qid, 'error': str(e)}})
                db.add_history(conn_id, stmt, 0, 0, 'error')
            except asyncio.CancelledError:
                ctx.status = 'cancelled'
                db.add_history(conn_id, stmt, 0, 0, 'cancelled')
                await safe_emit({'event': 'query.error',
                                 'data': {'query_id': qid, 'error': '已取消'}})
            except Exception as e:  # 驱动级未预期错误
                ctx.status = 'error'
                ctx.error = str(e)
                await safe_emit({'event': 'query.error', 'data': {'query_id': qid, 'error': str(e)}})
                db.add_history(conn_id, stmt, 0, 0, 'error')
            finally:
                asyncio.get_running_loop().call_later(
                    TTL_SECONDS, lambda: self._queries.pop(qid, None))

        ctx.task = asyncio.create_task(run())
        return qid

    def page(self, query_id: str, offset: int, limit: int) -> dict[str, Any]:
        ctx = self.get(query_id)
        # 边界防御:负 offset / 非法 limit 不该让切片行为变成"取尾部"
        offset = max(offset, 0)
        limit = max(limit, 1)
        first = ctx.first_rows
        if not first:
            return {'columns': [], 'rows': [], 'has_more': False,
                    'truncated': False, 'status': ctx.status}
        rows = first.rows[offset:offset + limit]
        # has_more 只表示缓冲区内还有未拉取的行;驱动层截断(BUFFER_CAP)单独用
        # truncated 表达 —— 旧实现把两者 or 在一起,截断后"加载更多"永远返回空页
        return {'columns': first.columns, 'rows': rows,
                'has_more': offset + limit < len(first.rows),
                'truncated': first.truncated,
                'total_buffered': len(first.rows), 'status': ctx.status}

    async def cancel(self, query_id: str) -> None:
        ctx = self.get(query_id)
        task = ctx.task
        if task is None or task.done():
            return  # 已结束的查询无需取消,也不应为此断开健康连接
        driver = manager.get_cached(ctx.conn_id)
        if driver is not None:
            try:
                await driver.cancel()
            except Exception:
                pass  # 取消失败降级:直接杀任务断连
        # cancel() 返回 False = 任务在竞态窗口内刚好完成,此时不应 evict 健康连接
        if task.cancel():
            await manager.evict(ctx.conn_id)


query_service = QueryService()
