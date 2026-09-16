"""杂项:health(服务探活)/ serve(拉起本机服务)/ settings(通用设置)。"""
from __future__ import annotations

import os

import typer

from ..errors import CliError, handle_cli_error
from ..output import make_console, print_json, print_rows, resolve_list_format
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

    注意:pip install . 场景下默认数据目录会落在 site-packages 旁,
    建议显式设置 DBSHOOTER_DATA_DIR。
    """
    import uvicorn
    uvicorn.run('backend.app.main:app',
                host=host if host is not None else os.environ.get('DBSHOOTER_HOST', '0.0.0.0'),
                port=port if port is not None else int(os.environ.get('DBSHOOTER_PORT', '5718')),
                reload=dev or os.environ.get('DBSHOOTER_DEV') == '1')


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
