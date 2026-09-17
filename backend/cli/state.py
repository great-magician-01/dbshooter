"""命令全局状态:根命令 callback 收集全局选项,子命令经 ctx.obj 读取。"""
from __future__ import annotations

from dataclasses import dataclass

import typer

from .client import ApiClient, make_client


@dataclass
class CliState:
    server: str | None = None
    token: str | None = None
    timeout: float = 60.0
    no_color: bool = False

    def client(self) -> ApiClient:
        return make_client(self.server, self.token, self.timeout)


def get_state(ctx: typer.Context) -> CliState:
    """子命令取全局状态;根 callback 保证已写入 CliState。"""
    obj = ctx.obj
    assert isinstance(obj, CliState)
    return obj
