"""CLI 统一错误与退出码约定。

0 成功 / 1 业务失败 / 2 用法错误(typer 自带) / 3 连不上服务 / 4 未授权
"""
from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar, cast

import httpx
import typer

EXIT_BUSINESS = 1
EXIT_UNREACHABLE = 3
EXIT_UNAUTHORIZED = 4

_F = TypeVar('_F', bound=Callable[..., Any])


class CliError(Exception):
    """命令失败:message 打到 stderr,按 code 退出。"""

    def __init__(self, message: str, code: int = EXIT_BUSINESS):
        super().__init__(message)
        self.message = message
        self.code = code


def mask_url(url: str) -> str:
    """报错信息里的服务地址脱敏:REST 与 WS 统一走这里,避免两处各写一份漂移。

    剥掉两段:
    - `?查询串`——用户把令牌写成 `-s http://host/?token=XXX` 时,httpx 的 str(URL)
      与 websockets 的 InvalidURI 消息都会原样带出,直接进终端/CI 日志就是泄漏;
    - `user:pass@` 的 userinfo——同理。
    """
    no_query = url.split('?')[0]
    scheme, sep, rest = no_query.partition('://')
    if sep and '@' in rest:
        rest = rest.rsplit('@', 1)[1]
    return f'{scheme}{sep}{rest}' if sep else no_query


def handle_cli_error(fn: _F) -> _F:
    """命令装饰器:CliError → stderr + 指定退出码;httpx/IO 异常统一兜底成业务失败(1),
    不向外抛裸 traceback。functools.wraps 保留签名,typer 靠注解生成参数。"""
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except CliError as e:
            typer.echo(f'错误: {e.message}', err=True)
            raise typer.Exit(e.code)
        except httpx.HTTPError as e:
            # ReadTimeout / RemoteProtocolError 等(client.py 只收了连接级,读超时不在其列)
            typer.echo(f'错误: 请求失败: {e}(可调大 --timeout)', err=True)
            raise typer.Exit(EXIT_BUSINESS)
        except OSError as e:
            # 本地文件读写失败(-f / -o / export 落盘等)
            typer.echo(f'错误: 文件操作失败: {e}', err=True)
            raise typer.Exit(EXIT_BUSINESS)
    return cast(_F, wrapper)
