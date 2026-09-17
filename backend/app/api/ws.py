"""WebSocket 通道:查询执行推送 + AI 流式输出。

协议:客户端发 {"id": "<req-id>", "type": "query.execute|ai.text2sql", "payload": {...}}
服务端推送 {"id": "<req-id>", "event": "...", "data": {...}}

约定:任何请求要么收到终态事件(query.done / query.error / ai.done / ai.error / error),
要么连接本身断开 —— handler 内的未预期异常一律兜底转成 error 事件,不让前端悬死。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from fastapi import WebSocket, WebSocketDisconnect
from starlette.datastructures import Headers

from .. import config, db, security
from ..drivers.base import DriverBase
from ..services import ai_service, ai_tools
from ..services.ai_tools import ToolResult
from ..services.connection_manager import manager
from ..services.query_service import query_service

log = logging.getLogger('dbshooter.ws')

# 单条 SQL 文本上限(1 MiB):WS 消息体本身有 uvicorn ws_max_size 兜底,
# 应用层再卡一道,避免巨型 payload 直接进驱动与历史表
MAX_STMT_LEN = 1_000_000
# 单 WS 连接的最大并发 handler 数,超出直接拒绝(背压,防单连接无限堆任务)
MAX_WS_TASKS = 16
# 心跳间隔:前端据此做半开连接检测(NAT/反代静默断链时浏览器不会派发 close)
HEARTBEAT_SECONDS = 25


def _origin_allowed(headers: Headers) -> bool:
    """浏览器跨站 WebSocket 不受同源策略保护:校验 Origin 与 Host 同源,
    防止恶意网页在用户浏览器里直连本机/内网的 DBShooter。
    非浏览器客户端(CLI)不带 Origin,放行(令牌仍强制)。"""
    origin = headers.get('origin')
    if not origin:
        return True
    host = (headers.get('host') or '').rsplit(':', 1)[0].strip('[]').lower()
    try:
        ohost = (urlsplit(origin).hostname or '').lower()
    except ValueError:
        return False
    return bool(host) and ohost == host


async def _handle_query_execute(ws: WebSocket, req_id: str, payload: dict[str, Any],
                                send_lock: asyncio.Lock, active_queries: set[str]) -> None:
    async def emit(ev: dict[str, Any]) -> None:
        async with send_lock:
            await ws.send_json({'id': req_id, **ev})

    conn_id = payload.get('conn_id')
    stmt = payload.get('stmt')
    if not conn_id or not stmt:
        await emit({'event': 'query.error', 'data': {'error': '缺少 conn_id 或 stmt'}})
        return
    if len(str(stmt)) > MAX_STMT_LEN:
        await emit({'event': 'query.error',
                    'data': {'error': f'语句过长(上限 {MAX_STMT_LEN // 1_000_000} MiB)'}})
        return
    try:
        qid = await query_service.execute(str(conn_id), str(stmt), emit,
                                          schema=payload.get('schema'))
        # 登记在途查询,WS 断开时由端点统一取消(execute 内部无 await,
        # create_task 一定先于本行完成,不存在登记窗口);任务结束即摘出,
        # 长连接下集合不无限增长
        active_queries.add(qid)
        ctx = query_service.get(qid)
        if ctx.task is not None:
            ctx.task.add_done_callback(lambda _t, q=qid: active_queries.discard(q))
    except Exception as e:
        # execute 内部已把执行期错误转为 query.error;这里兜底同步建连等失败
        await emit({'event': 'query.error', 'data': {'error': str(e)}})


async def _handle_ai(ws: WebSocket, req_id: str, payload: dict[str, Any],
                     send_lock: asyncio.Lock, active_queries: set[str]) -> None:
    async def emit(event: str, data: dict[str, Any]) -> None:
        async with send_lock:
            await ws.send_json({'id': req_id, 'event': event, 'data': data})

    provider = db.get_active_provider()
    if not provider:
        await emit('ai.error', {'message': '尚未配置生效的 AI Provider,请先到 设置 → AI Provider'})
        return
    session_id = payload.get('session_id')
    question = str(payload.get('question') or '').strip()
    if not session_id or not question:
        await emit('ai.error', {'message': '缺少 session_id 或 question'})
        return
    if not db.one('SELECT id FROM ai_sessions WHERE id=?', (str(session_id),)):
        # 外键约束会把违例变成裸异常,提前给出可读错误
        await emit('ai.error', {'message': f'会话不存在: {session_id}'})
        return

    t0 = time.monotonic()
    try:
        # 连接存在且为 SQL 类驱动时启用自助查表工具(list_tables / describe_table);
        # 用户在问题里点名表,由 AI 自行查结构,不再手动附加 DDL
        conn_id = payload.get('conn_id')
        dialect = 'SQL'
        driver: DriverBase | None = None
        if conn_id:
            try:
                d = await manager.get(str(conn_id))
                if d.editor_mode == 'sql':   # mongo/redis 不适用 text2sql 工具
                    driver = d
                    dialect = ai_service.DIALECTS.get(d.kind, 'SQL')
            except Exception as e:
                log.warning('加载连接失败: %s', e)

        db.add_message(str(session_id), 'user', question)
        messages = ai_service.build_messages(question, dialect,
                                             tools_available=driver is not None)
        await emit('ai.started', {})
        trace: list[dict[str, Any]] = []
        if driver is not None:
            drv = driver   # 闭包内收窄为非空

            async def on_token(t: str) -> None:
                await emit('ai.token', {'delta': t})

            async def on_tool(ev: dict[str, Any]) -> None:
                await emit('ai.tool', ev)

            async def run_tool(name: str, args: dict[str, Any]) -> ToolResult:
                return await ai_tools.run_tool(drv, name, args)

            full, trace = await ai_service.run_agent(
                provider, messages, tools=ai_tools.tool_specs(drv.kind),
                run_tool=run_tool, on_token=on_token, on_tool=on_tool)
        else:
            parts: list[str] = []
            async for token in ai_service.stream_chat(provider, messages):
                parts.append(token)
                await emit('ai.token', {'delta': token})
            full = ''.join(parts)
        sql = ai_service.extract_sql(full)
        elapsed = int((time.monotonic() - t0) * 1000)
        db.add_message(str(session_id), 'assistant',
                       json.dumps({'text': full, 'sql': sql, 'tools': trace}, ensure_ascii=False),
                       elapsed_ms=elapsed)
        await emit('ai.done', {'text': full, 'sql': sql, 'elapsed_ms': elapsed, 'tools': trace})
    except Exception as e:
        await emit('ai.error', {'message': f'调用 AI 失败: {e}'})


HANDLERS = {
    'query.execute': _handle_query_execute,
    'ai.text2sql': _handle_ai,
}


async def _run_handler(handler: Callable[[WebSocket, str, dict[str, Any], asyncio.Lock,
                                          set[str]], Awaitable[None]],
                       ws: WebSocket, req_id: str, payload: dict[str, Any],
                       send_lock: asyncio.Lock, active_queries: set[str]) -> None:
    """兜底网:handler 的未预期异常(畸形 payload、元数据层异常等)转为 error 事件。"""
    try:
        await handler(ws, req_id, payload, send_lock, active_queries)
    except Exception as e:
        log.exception('WS 请求处理失败(id=%s): %s', req_id, e)
        try:
            async with send_lock:
                await ws.send_json({'id': req_id, 'event': 'error',
                                    'data': {'message': f'处理失败: {e}'}})
        except Exception:
            pass  # 连接可能已断,尽力而为


async def _heartbeat(ws: WebSocket, send_lock: asyncio.Lock) -> None:
    """周期心跳:客户端以"任意 inbound 消息"做半开连接检测的看门狗。"""
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            async with send_lock:
                await ws.send_json({'id': '_hb', 'event': 'ping', 'data': {}})
        except Exception:
            return  # 连接已断:接收循环会感知并走清理,心跳任务安静退出即可


async def websocket_endpoint(ws: WebSocket) -> None:
    # 可选令牌:WS 通过 ?token= 鉴权(恒时比较)
    if config.ACCESS_TOKEN and not security.token_matches(ws.query_params.get('token')):
        await ws.close(code=4401)
        return
    # 跨站 WS 防护:浏览器页面必须与本服务同源(令牌未启用时的主要防线)
    if not _origin_allowed(ws.headers):
        await ws.close(code=4403)
        return
    await ws.accept()
    send_lock = asyncio.Lock()
    tasks: set[asyncio.Task] = set()
    active_queries: set[str] = set()   # 本连接发起的在途查询 id
    hb_task = asyncio.create_task(_heartbeat(ws, send_lock))
    try:
        while True:
            msg = await ws.receive_json()
            req_id = str(msg.get('id') or '')
            handler = HANDLERS.get(msg.get('type'))
            if not handler:
                async with send_lock:
                    await ws.send_json({'id': req_id, 'event': 'error',
                                        'data': {'message': f"未知消息类型: {msg.get('type')}"}})
                continue
            payload = msg.get('payload') or {}
            if not isinstance(payload, dict):
                async with send_lock:
                    await ws.send_json({'id': req_id, 'event': 'error',
                                        'data': {'message': 'payload 必须是对象'}})
                continue
            if len(tasks) >= MAX_WS_TASKS:
                async with send_lock:
                    await ws.send_json({'id': req_id, 'event': 'error',
                                        'data': {'message': '请求过于频繁,请等待进行中的请求完成'}})
                continue
            task = asyncio.create_task(
                _run_handler(handler, ws, req_id, payload, send_lock, active_queries))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.exception('WS 异常: %s', e)
        with contextlib.suppress(Exception):
            await ws.close()
    finally:
        hb_task.cancel()
        for t in tasks:
            t.cancel()
        # 断开即取消本连接的在途查询(取消 = driver.cancel + 杀任务 + 断连),
        # 否则客户端走了查询仍在数据库上继续跑;已完成的查询 cancel 是 no-op
        for qid in active_queries:
            with contextlib.suppress(Exception):
                await query_service.cancel(qid)
