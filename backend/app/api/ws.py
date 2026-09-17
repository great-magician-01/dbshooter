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

from fastapi import WebSocket, WebSocketDisconnect

from .. import config, db, security
from ..drivers.base import DriverBase
from ..services import ai_service, ai_tools
from ..services.ai_tools import ToolResult
from ..services.connection_manager import manager
from ..services.query_service import query_service

log = logging.getLogger('dbshooter.ws')


async def _handle_query_execute(ws: WebSocket, req_id: str, payload: dict[str, Any],
                                send_lock: asyncio.Lock) -> None:
    async def emit(ev: dict[str, Any]) -> None:
        async with send_lock:
            await ws.send_json({'id': req_id, **ev})

    conn_id = payload.get('conn_id')
    stmt = payload.get('stmt')
    if not conn_id or not stmt:
        await emit({'event': 'query.error', 'data': {'error': '缺少 conn_id 或 stmt'}})
        return
    try:
        await query_service.execute(str(conn_id), str(stmt), emit,
                                    schema=payload.get('schema'))
    except Exception as e:
        # execute 内部已把执行期错误转为 query.error;这里兜底同步建连等失败
        await emit({'event': 'query.error', 'data': {'error': str(e)}})


async def _handle_ai(ws: WebSocket, req_id: str, payload: dict[str, Any],
                     send_lock: asyncio.Lock) -> None:
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


async def _run_handler(handler: Callable[[WebSocket, str, dict[str, Any], asyncio.Lock],
                                         Awaitable[None]],
                       ws: WebSocket, req_id: str, payload: dict[str, Any],
                       send_lock: asyncio.Lock) -> None:
    """兜底网:handler 的未预期异常(畸形 payload、元数据层异常等)转为 error 事件。"""
    try:
        await handler(ws, req_id, payload, send_lock)
    except Exception as e:
        log.exception('WS 请求处理失败(id=%s): %s', req_id, e)
        try:
            async with send_lock:
                await ws.send_json({'id': req_id, 'event': 'error',
                                    'data': {'message': f'处理失败: {e}'}})
        except Exception:
            pass  # 连接可能已断,尽力而为


async def websocket_endpoint(ws: WebSocket) -> None:
    # 可选令牌:WS 通过 ?token= 鉴权(恒时比较)
    if config.ACCESS_TOKEN and not security.token_matches(ws.query_params.get('token')):
        await ws.close(code=4401)
        return
    await ws.accept()
    send_lock = asyncio.Lock()
    tasks: set[asyncio.Task] = set()
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
            task = asyncio.create_task(_run_handler(handler, ws, req_id, payload, send_lock))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    except WebSocketDisconnect:
        for t in tasks:
            t.cancel()
    except Exception as e:
        log.exception('WS 异常: %s', e)
        for t in tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await ws.close()
