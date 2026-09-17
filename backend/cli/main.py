"""dbs 命令入口:全局选项 + 命令装配。

两种用法:仓库内 python -m backend.cli;pip install . 后 dbs 命令(console script 指向 run)。
"""
from __future__ import annotations

import io
import os
import sys

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
    _fix_stdio()
    # NO_COLOR 惯例是"存在即生效",不走 typer 的布尔 envvar 解析
    ctx.obj = CliState(server=server, token=token, timeout=timeout,
                       no_color=no_color or bool(os.environ.get('NO_COLOR')))


def _fix_stdio() -> None:
    """Windows 管道/重定向场景统一 UTF-8:否则中文按 cp936 编解码,
    重定向含非 GBK 字符会直接 UnicodeEncodeError;stdout newline='' 防 csv 双 CR。

    只有 TextIOWrapper 支持 reconfigure,所以按 isinstance 判断。注意:测试用的
    click CliRunner 流(_NamedTextIOWrapper)正是其子类,跑测试时这里会被真的执行
    (errors='replace' 会把编码错误吞成替换字符)——即编码类问题在测试里不暴露,
    别拿测试通过代替 Windows 真实控制台的验证。"""
    try:
        if isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace', newline='')
        if isinstance(sys.stderr, io.TextIOWrapper):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        if isinstance(sys.stdin, io.TextIOWrapper):
            sys.stdin.reconfigure(encoding='utf-8', errors='replace')
    except (ValueError, OSError):
        pass   # 已关闭/不可配置的流不强求


app.add_typer(conn.app, name='conn')
app.add_typer(ai.app, name='ai')
app.add_typer(misc.settings_app, name='settings')
app.command('health')(misc.health)
app.command('serve')(misc.serve)
app.command('tree')(meta.tree)
app.command('ddl')(meta.ddl)
app.command('key')(meta.key)
app.command('query')(query_cmd.query)
app.command('export')(query_cmd.export)
app.command('history')(query_cmd.history)


def run() -> None:
    """console script 入口。"""
    app()
