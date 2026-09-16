"""WebSocket 通道:查询执行推送 + AI 流式输出。

协议:客户端发 {"id": "<req-id>", "type": "query.execute|ai.text2sql", "payload": {...}}
服务端推送 {"id": "<req-id>", "event": "...", "data": {...}}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

from .. import config, db
from ..services import ai_service
from ..services.connection_manager import manager
from ..services.query_service import query_service

log = logging.getLogger('dbshooter.ws')


async def _handle_query_execute(ws: WebSocket, req_id: str, payload: dict, send_lock: asyncio.Lock):
    async def emit(ev: dict) -> None:
        async with send_lock:
            await ws.send_json({'id': req_id, **ev})
    await query_service.execute(payload['conn_id'], payload['stmt'], emit,
                                schema=payload.get('schema'))


async def _handle_ai(ws: WebSocket, req_id: str, payload: dict, send_lock: asyncio.Lock):
    async def emit(event: str, data: dict) -> None:
        async with send_lock:
            await ws.send_json({'id': req_id, 'event': event, 'data': data})

    provider = db.get_active_provider()
    if not provider:
        await emit('ai.error', {'message': '尚未配置生效的 AI Provider,请先到 设置 → AI Provider'})
        return
    session_id = payload.get('session_id')
    question = payload.get('question', '').strip()
    if not session_id or not question:
        await emit('ai.error', {'message': '缺少 session_id 或 question'})
        return

    # schema 上下文:用户选中的表 → DDL
    ddl = ''
    tables = payload.get('tables') or []
    conn_id = payload.get('conn_id')
    dialect = 'SQL'
    if conn_id and tables:
        try:
            driver = await manager.get(conn_id)
            ddl = await driver.ddl(tables)
            dialect = ai_service.DIALECTS.get(driver.kind, 'SQL')
        except Exception as e:
            log.warning('加载 DDL 失败: %s', e)

    t0 = time.monotonic()
    db.add_message(session_id, 'user', question)
    messages = ai_service.build_messages(question, ddl, dialect)
    await emit('ai.started', {})
    parts: list[str] = []
    try:
        async for token in ai_service.stream_chat(provider, messages):
            parts.append(token)
            await emit('ai.token', {'delta': token})
    except Exception as e:
        await emit('ai.error', {'message': f'调用 AI 失败: {e}'})
        return
    full = ''.join(parts)
    sql = ai_service.extract_sql(full)
    elapsed = int((time.monotonic() - t0) * 1000)
    db.add_message(session_id, 'assistant',
                   json.dumps({'text': full, 'sql': sql}, ensure_ascii=False),
                   elapsed_ms=elapsed)
    await emit('ai.done', {'text': full, 'sql': sql, 'elapsed_ms': elapsed})


HANDLERS = {
    'query.execute': _handle_query_execute,
    'ai.text2sql': _handle_ai,
}


async def websocket_endpoint(ws: WebSocket) -> None:
    # 可选令牌:WS 通过 ?token= 鉴权
    if config.ACCESS_TOKEN and ws.query_params.get('token') != config.ACCESS_TOKEN:
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
            task = asyncio.create_task(handler(ws, req_id, msg.get('payload') or {}, send_lock))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    except WebSocketDisconnect:
        for t in tasks:
            t.cancel()
    except Exception as e:
        log.exception('WS 异常: %s', e)
        for t in tasks:
            t.cancel()
