"""杂项:health(服务探活)/ serve(拉起本机服务)/ settings(通用设置)。"""
from __future__ import annotations

import os

import typer

from ..errors import CliError, handle_cli_error
from ..output import make_console, print_json, print_rows, resolve_list_format, warn
from ..state import get_state

settings_app = typer.Typer(help='通用设置(与 Web 端共享存储)', no_args_is_help=True)


@handle_cli_error
def health(ctx: typer.Context) -> None:
    """服务探活 + 已注册驱动列表。"""
    r = get_state(ctx).client().get('/api/health')
    typer.echo('服务正常,驱动: ' + ', '.join(r['drivers']))


@handle_cli_error
def serve(ctx: typer.Context,
          host: str | None = typer.Option(None, '--host'),
          port: int | None = typer.Option(None, '--port'),
          dev: bool = typer.Option(False, '--dev', help='热重载(等价 DBSHOOTER_DEV=1)')) -> None:
    """启动本机服务(等价 python run.py)。

    监听地址:--host > DBSHOOTER_HOST > 127.0.0.1(默认只听本机;要对外提供访问
    需显式指定,且未设 DBSHOOTER_TOKEN 时会启动即警告)。端口同理:--port > DBSHOOTER_PORT > 5718。

    注意:pip install . 场景下默认数据目录会落在 site-packages 旁,
    建议显式设置 DBSHOOTER_DATA_DIR。
    """
    import uvicorn
    resolved_host = host if host is not None else os.environ.get('DBSHOOTER_HOST', '127.0.0.1')
    resolved_port = port if port is not None else int(os.environ.get('DBSHOOTER_PORT', '5718'))
    _warn_if_exposed(resolved_host)
    uvicorn.run('backend.app.main:app', host=resolved_host, port=resolved_port,
                reload=dev or os.environ.get('DBSHOOTER_DEV') == '1')


def _warn_if_exposed(host: str) -> None:
    """监听非本机地址时提示风险:没设令牌等于把数据库连接面板公开给整个网络。"""
    if host in ('127.0.0.1', 'localhost', '::1'):
        return
    if os.environ.get('DBSHOOTER_TOKEN'):
        warn(f'监听 {host},已启用令牌校验(DBSHOOTER_TOKEN)')
        return
    warn(f'警告:监听 {host} 且未设置 DBSHOOTER_TOKEN,同网络内任何人都能访问本服务的'
         '连接与数据。需要暴露时请设置随机令牌后再启动:'
         'DBSHOOTER_TOKEN=<随机字符串> dbs serve --host ' + host)


@settings_app.command('get')
@handle_cli_error
def settings_get(ctx: typer.Context,
                 key: str | None = typer.Argument(None, help='缺省列出全部'),
                 fmt: str | None = typer.Option(None, '--format', help='输出格式: table/json')) -> None:
    """读取设置。"""
    st = get_state(ctx)
    values: dict[str, str] = st.client().get('/api/settings')['values']
    if key is not None:
        if key not in values:
            raise CliError(f'未设置: {key}')
        print(values[key])
        return
    if resolve_list_format(fmt) == 'json':
        print_json(values)
        return
    print_rows(['键', '值'], [[k, v] for k, v in values.items()], make_console(st.no_color))


@settings_app.command('set')
@handle_cli_error
def settings_set(ctx: typer.Context,
                 key: str = typer.Argument(...),
                 value: str = typer.Argument(...)) -> None:
    """写入设置(字符串值;主题等对 CLI 无意义的键原样透传)。"""
    get_state(ctx).client().post('/api/settings/save', {'values': {key: value}})
    typer.echo(f'已保存: {key}')
