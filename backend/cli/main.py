"""dbs 命令入口:全局选项 + 命令装配。

两种用法:仓库内 python -m backend.cli;pip install . 后 dbs 命令(console script 指向 run)。
"""
from __future__ import annotations

import os

import typer

from .commands import ai, conn, meta, misc, query as query_cmd
from .state import CliState

app = typer.Typer(name='dbs',
                  help='DBShooter 命令行客户端(现有 REST/WS 接口的薄客户端)',
                  no_args_is_help=True, add_completion=False,
                  pretty_exceptions_enable=False)


@app.callback()
def _global(ctx: typer.Context,
            server: str | None = typer.Option(None, '--server', '-s', envvar='DBSHOOTER_URL',
                                              help='服务地址,默认 http://127.0.0.1:5718'),
            token: str | None = typer.Option(None, '--token', '-t', envvar='DBSHOOTER_TOKEN',
                                             help='访问令牌,与服务端 DBSHOOTER_TOKEN 一致'),
            timeout: float = typer.Option(60.0, '--timeout', help='读超时(秒)'),
            no_color: bool = typer.Option(False, '--no-color', help='关闭颜色输出')) -> None:
    """DBShooter 命令行客户端。全局选项需写在子命令之前,如 dbs -s URL conn list。"""
    # NO_COLOR 惯例是"存在即生效",不走 typer 的布尔 envvar 解析
    ctx.obj = CliState(server=server, token=token, timeout=timeout,
                       no_color=no_color or bool(os.environ.get('NO_COLOR')))


app.add_typer(conn.app, name='conn')
app.add_typer(ai.app, name='ai')
app.command('health')(misc.health)
app.command('tree')(meta.tree)
app.command('ddl')(meta.ddl)
app.command('key')(meta.key)
app.command('query')(query_cmd.query)
app.command('export')(query_cmd.export)
app.command('history')(query_cmd.history)


def run() -> None:
    """console script 入口。"""
    app()
