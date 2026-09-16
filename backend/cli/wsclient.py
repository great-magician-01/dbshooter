"""WebSocket 客户端:仅用于 ai.text2sql(AI 目前只有 WS 通道)。

用 websockets 的同步客户端,与同步 typer 命令模型一致;函数级封装便于测试替换。
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Iterator

from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect as _ws_connect

from .errors import EXIT_UNAUTHORIZED, EXIT_UNREACHABLE, CliError


def ws_url(base: str, token: str | None) -> str:
    """http(s) base → ws(s):///ws,令牌走 ?token=(与服务端约定一致)。"""
    b = base.rstrip('/')
    if b.startswith('https://'):
        b = 'wss://' + b[len('https://'):]
    elif b.startswith('http://'):
        b = 'ws://' + b[len('http://'):]
    else:
        b = 'ws://' + b
    url = b + '/ws'
    return f'{url}?token={token}' if token else url


def stream_ai_events(url: str, payload: dict[str, Any],
                     timeout: float) -> Iterator[tuple[str, dict[str, Any]]]:
    """发一条 ai.text2sql,按请求 id 过滤,逐事件 yield 到 ai.done / ai.error 为止。"""
    req_id = uuid.uuid4().hex
    try:
        with _ws_connect(url, open_timeout=10, close_timeout=5) as ws:
            ws.send(json.dumps({'id': req_id, 'type': 'ai.text2sql', 'payload': payload}))
            while True:
                msg = json.loads(ws.recv(timeout=timeout))
                if msg.get('id') != req_id:
                    continue
                event: str = msg.get('event') or ''
                yield event, msg.get('data') or {}
                if event in ('ai.done', 'ai.error', 'error'):
                    return
    except InvalidStatus as e:
        # 服务端令牌校验失败时在 accept 前 close → 握手被拒(403)
        status = e.response.status_code if e.response is not None else 0
        if status in (401, 403):
            raise CliError('未授权:请用 --token 或 DBSHOOTER_TOKEN 提供访问令牌',
                           EXIT_UNAUTHORIZED) from e
        raise CliError(f'WS 握手失败({status}): {url}') from e
    except (ConnectionRefusedError, TimeoutError, OSError) as e:
        raise CliError(f'无法连接服务 {url.split("/ws")[0]}:'
                       '先启动服务,或用 --server / DBSHOOTER_URL 指定地址',
                       EXIT_UNREACHABLE) from e
