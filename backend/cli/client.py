"""服务端 REST 客户端:base_url / 鉴权头 / 超时 / 错误归一化。

inner 是可替换的 httpx.Client 实例——测试时用 FastAPI TestClient 直插内存 app,不起真实端口。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

import httpx

from .errors import EXIT_UNAUTHORIZED, EXIT_UNREACHABLE, CliError, mask_url

DEFAULT_SERVER = 'http://127.0.0.1:5718'

_UNREACHABLE_HINT = ('无法连接服务 {base}:连接被拒绝。'
                     '先启动服务(python run.py / dbs serve),或用 --server / DBSHOOTER_URL 指定地址')


def _as_json(resp: httpx.Response) -> Any:
    """2xx 但响应体不是 JSON(如 --server 指到了别的 web 服务/反代欢迎页):

    归一化为可读错误(退出码 3),不让 json.JSONDecodeError 裸穿到终端。"""
    try:
        return resp.json()
    except ValueError as e:   # json.JSONDecodeError 是其子类
        raise CliError('响应不是 JSON(可能不是 dbshooter 服务,检查 --server)',
                       EXIT_UNREACHABLE) from e


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


def _invalid_url(e: httpx.InvalidURL) -> CliError:
    """地址本身非法(端口写成 57l8 之类):请求压根发不出去,给中文报错。

    注意 httpx.InvalidURL 直接继承 Exception、不是 HTTPError 子类(MRO 里没有),
    handle_cli_error 与这里的 ConnectError 分支都收不到,不转就是裸 traceback。"""
    return CliError(f'无效的服务地址: {e}(检查 --server / DBSHOOTER_URL)')


class ApiClient:
    """对 /api 的最薄封装,只负责传输与错误转换,不含任何业务逻辑。"""

    def __init__(self, inner: httpx.Client):
        self._inner = inner

    def get(self, url: str, **params: Any) -> Any:
        try:
            resp = self._inner.get(url, params={k: v for k, v in params.items() if v is not None})
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise CliError(self._unreachable(), EXIT_UNREACHABLE) from e
        except httpx.InvalidURL as e:
            raise _invalid_url(e) from e
        _raise_for(resp)
        return _as_json(resp)

    def post(self, url: str, body: dict[str, Any] | None = None) -> Any:
        try:
            resp = self._inner.post(url, json=body or {})
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise CliError(self._unreachable(), EXIT_UNREACHABLE) from e
        except httpx.InvalidURL as e:
            raise _invalid_url(e) from e
        _raise_for(resp)
        return _as_json(resp)

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
        except httpx.InvalidURL as e:
            raise _invalid_url(e) from e

    def _unreachable(self) -> str:
        # 地址要脱敏:str(URL) 会带出 ?token= 与 user:pass@(见 errors.mask_url)
        return _UNREACHABLE_HINT.format(base=mask_url(str(self._inner.base_url)).rstrip('/'))


def resolve_base(server: str | None) -> str:
    """flag > 环境变量 > 默认值。"""
    return (server or os.environ.get('DBSHOOTER_URL') or DEFAULT_SERVER).rstrip('/')


def resolve_token(token: str | None) -> str | None:
    return token if token is not None else (os.environ.get('DBSHOOTER_TOKEN') or None)


def make_client(server: str | None, token: str | None, timeout: float) -> ApiClient:
    base = resolve_base(server)
    tok = resolve_token(token)
    headers = {'Authorization': f'Bearer {tok}'} if tok else {}
    try:
        # httpx 在构造 Client 时就会解析 base_url,端口非法(如 :57l8)在这里就抛 InvalidURL
        inner = httpx.Client(base_url=base, headers=headers,
                             timeout=httpx.Timeout(timeout, connect=5.0))
    except httpx.InvalidURL as e:
        raise _invalid_url(e) from e
    return ApiClient(inner)
