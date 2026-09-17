"""连接管理:list / add / update / delete / test。"""
from __future__ import annotations

import json
import sys
from typing import Any

import typer

from ..errors import CliError, handle_cli_error
from ..output import FMT_CHOICES, make_console, print_json, print_rows, resolve_list_format
from ..resolve import resolve_conn
from ..state import get_state

app = typer.Typer(help='连接管理', no_args_is_help=True)

_TYPE_HELP = 'sqlite | mysql | pg | redis | mongo'
_FMT_OPT = typer.Option(None, '--format', help=f'输出格式: {"/".join(FMT_CHOICES)}')


def _parse_params(pairs: list[str] | None) -> dict[str, Any]:
    """--param k=v:值先按 JSON 解析(数字/布尔/带引号字符串),失败则当普通字符串。"""
    out: dict[str, Any] = {}
    for p in pairs or []:
        if '=' not in p:
            raise CliError(f'--param 需要 k=v 形式: {p}')
        k, _, v = p.partition('=')
        try:
            out[k.strip()] = json.loads(v)
        except json.JSONDecodeError:
            out[k.strip()] = v
    return out


def _ask_password(type_: str) -> str:
    """未显式给密码且是交互终端时提示输入(sqlite 无密码,脚本/管道场景留空)。"""
    if type_ == 'sqlite' or not sys.stdin.isatty():
        return ''
    return typer.prompt('密码(留空跳过)', hide_input=True, default='', show_default=False)


def _addr(c: dict[str, Any]) -> str:
    """连接地址摘要,用于列表展示。"""
    p: dict[str, Any] = c.get('params') or {}
    if c['type'] == 'sqlite':
        return str(p.get('path', ''))
    base = f"{c.get('host', '')}:{c.get('port') or ''}"
    if c['type'] == 'mongo':
        return str(p.get('uri') or base)
    if c['type'] == 'redis':
        return f"{base}/db{p.get('db', 0)}"
    return f"{base}/{c.get('database', '')}"


@app.command('list')
@handle_cli_error
def list_(ctx: typer.Context, fmt: str | None = _FMT_OPT) -> None:
    """列出所有连接。"""
    st = get_state(ctx)
    items: list[dict[str, Any]] = st.client().get('/api/connections')['items']
    if resolve_list_format(fmt) == 'json':
        print_json(items)
        return
    print_rows(['ID', '名称', '类型', '地址', '只读'],
               [[c['id'][:8], c['name'], c['type'], _addr(c), '是' if c['readonly'] else '']
                for c in items],
               make_console(st.no_color))


@app.command()
@handle_cli_error
def add(ctx: typer.Context,
        name: str = typer.Option(..., '--name', help='连接名'),
        type_: str = typer.Option(..., '--type', help=_TYPE_HELP),
        host: str = typer.Option('', '--host'),
        port: int | None = typer.Option(None, '--port'),
        database: str = typer.Option('', '--database'),
        username: str = typer.Option('', '--username'),
        password: str | None = typer.Option(None, '--password', help='缺省时交互式隐式输入'),
        param: list[str] | None = typer.Option(None, '--param', help='k=v,可重复;sqlite 用 path=...'),
        readonly: bool = typer.Option(False, '--readonly', help='只读模式')) -> None:
    """新增连接。例:dbs conn add --name 本地 --type sqlite --param path=/tmp/a.db"""
    if password is None:
        password = _ask_password(type_)
    body = {'name': name, 'type': type_, 'host': host, 'port': port,
            'database': database, 'username': username, 'password': password,
            'params': _parse_params(param), 'readonly': readonly}
    item = get_state(ctx).client().post('/api/connections', body)['item']
    typer.echo(f"已创建连接 {item['name']} (id {item['id'][:8]})")


@app.command()
@handle_cli_error
def update(ctx: typer.Context,
           ref: str = typer.Argument(..., metavar='CONN', help='连接 id / id 前缀 / 名称'),
           name: str | None = typer.Option(None, '--name'),
           host: str | None = typer.Option(None, '--host'),
           port: int | None = typer.Option(None, '--port'),
           database: str | None = typer.Option(None, '--database'),
           username: str | None = typer.Option(None, '--username'),
           password: str | None = typer.Option(None, '--password'),
           param: list[str] | None = typer.Option(None, '--param', help='k=v,合并进现有 params'),
           readonly: bool | None = typer.Option(None, '--readonly/--no-readonly')) -> None:
    """更新连接:只改传入的字段,密码不传则不修改。"""
    client = get_state(ctx).client()
    old = resolve_conn(client, ref)
    params: dict[str, Any] = dict(old.get('params') or {})
    params.update(_parse_params(param))
    body = {'id': old['id'],
            'name': name if name is not None else old['name'],
            'type': old['type'],
            'host': host if host is not None else old['host'],
            'port': port if port is not None else old['port'],
            'database': database if database is not None else old['database'],
            'username': username if username is not None else old['username'],
            'password': password or '',   # 空 = 服务端保留原密码
            'params': params,
            'readonly': old['readonly'] if readonly is None else readonly}
    client.post('/api/connections/update', body)
    typer.echo(f"已更新 {old['name']} ({old['id'][:8]})")


@app.command()
@handle_cli_error
def delete(ctx: typer.Context,
           ref: str = typer.Argument(..., metavar='CONN'),
           yes: bool = typer.Option(False, '--yes', '-y', help='跳过确认')) -> None:
    """删除连接。"""
    client = get_state(ctx).client()
    row = resolve_conn(client, ref)
    if not yes and not typer.confirm(f"删除连接 {row['name']} ({row['id'][:8]})?"):
        typer.echo('已取消')
        return
    client.post('/api/connections/delete', {'id': row['id']})
    typer.echo('已删除')


@app.command()
@handle_cli_error
def test(ctx: typer.Context,
         ref: str | None = typer.Argument(None, metavar='CONN'),
         type_: str | None = typer.Option(None, '--type', help=_TYPE_HELP),
         host: str = typer.Option('', '--host'),
         port: int | None = typer.Option(None, '--port'),
         database: str = typer.Option('', '--database'),
         username: str = typer.Option('', '--username'),
         password: str | None = typer.Option(None, '--password'),
         param: list[str] | None = typer.Option(None, '--param')) -> None:
    """测试连接:传已保存的 CONN,或用 --type/--param 等传临时配置。"""
    client = get_state(ctx).client()
    if ref is not None and type_ is None:
        body: dict[str, Any] = {'id': resolve_conn(client, ref)['id']}
    elif type_ is not None:
        if password is None:
            password = _ask_password(type_)
        body = {'config': {'name': 'cli-test', 'type': type_, 'host': host, 'port': port,
                           'database': database, 'username': username, 'password': password,
                           'params': _parse_params(param), 'readonly': False}}
    else:
        raise CliError('请提供连接(id / id 前缀 / 名称),或用 --type 指定临时配置')
    r = client.post('/api/connections/test', body)
    if r['ok']:
        typer.echo(f"连接成功: {r['message']}")
        return
    raise CliError(f"连接失败: {r['message']}")
