"""服务端 REST 客户端:base_url / 鉴权头 / 超时 / 错误归一化。

inner 是可替换的 httpx.Client 实例——测试时用 FastAPI TestClient 直插内存 app,不起真实端口。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

import httpx

from .errors import EXIT_UNAUTHORIZED, EXIT_UNREACHABLE, CliError

DEFAULT_SERVER = 'http://127.0.0.1:5718'

_UNREACHABLE_HINT = ('无法连接服务 {base}:连接被拒绝。'
                     '先启动服务(python run.py / dbs serve),或用 --server / DBSHOOTER_URL 指定地址')


def _raise_for(resp: httpx.Response) -> None:
    """非 2xx 归一化为 CliError;业务错误沿用 FastAPI 的 {"detail": ...}。"""
    if resp.status_code < 400:
        return
    if resp.status_code == 401:
        raise CliError('未授权:请用 --token 或 DBSHOOTER_TOKEN 提供访问令牌', EXIT_UNAUTHORIZED)
    detail: Any
    try:
        detail = resp.json().get('detail')
    except Exception:  # 非 JSON 错误页(如反代 502)
        detail = resp.text[:200]
    raise CliError(f'请求失败({resp.status_code}): {detail}')


class ApiClient:
    """对 /api 的最薄封装,只负责传输与错误转换,不含任何业务逻辑。"""

    def __init__(self, inner: httpx.Client):
        self._inner = inner

    def get(self, url: str, **params: Any) -> Any:
        try:
            resp = self._inner.get(url, params={k: v for k, v in params.items() if v is not None})
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise CliError(self._unreachable(), EXIT_UNREACHABLE) from e
        _raise_for(resp)
        return resp.json()

    def post(self, url: str, body: dict[str, Any] | None = None) -> Any:
        try:
            resp = self._inner.post(url, json=body or {})
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise CliError(self._unreachable(), EXIT_UNREACHABLE) from e
        _raise_for(resp)
        return resp.json()

    @contextmanager
    def stream(self, url: str, body: dict[str, Any]) -> Iterator[httpx.Response]:
        """流式 POST(CSV 导出):大结果集分块落盘,不进内存。"""
        try:
            with self._inner.stream('POST', url, json=body) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    _raise_for(resp)
                yield resp
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise CliError(self._unreachable(), EXIT_UNREACHABLE) from e

    def _unreachable(self) -> str:
        return _UNREACHABLE_HINT.format(base=str(self._inner.base_url).rstrip('/'))


def make_client(server: str | None, token: str | None, timeout: float) -> ApiClient:
    """按 flag > 环境变量 > 默认值 解析服务地址与令牌。"""
    base = (server or os.environ.get('DBSHOOTER_URL') or DEFAULT_SERVER).rstrip('/')
    tok = token if token is not None else (os.environ.get('DBSHOOTER_TOKEN') or None)
    headers = {'Authorization': f'Bearer {tok}'} if tok else {}
    return ApiClient(httpx.Client(base_url=base, headers=headers,
                                  timeout=httpx.Timeout(timeout, connect=5.0)))
