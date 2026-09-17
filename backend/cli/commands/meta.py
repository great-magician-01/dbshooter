"""元数据与结构:tree / ddl / key(Redis)。"""
from __future__ import annotations

from typing import Any

import typer

from ..client import ApiClient
from ..errors import CliError, handle_cli_error
from ..output import print_json, resolve_list_format
from ..resolve import resolve_conn
from ..state import get_state

# 递归展开层数上限,防止大库一次刷爆终端
MAX_DEPTH = 5


def _fetch(client: ApiClient, cid: str, path: str) -> list[dict[str, Any]]:
    return client.get(f'/api/connections/{cid}/metadata', path=path)['items']


@handle_cli_error
def tree(ctx: typer.Context,
         conn: str = typer.Argument(..., metavar='CONN', help='连接 id / id 前缀 / 名称'),
         path: str = typer.Option('', '--path', help='起始路径,如 shop.users'),
         depth: int = typer.Option(1, '--depth', help=f'递归展开层数(上限 {MAX_DEPTH})'),
         fmt: str | None = typer.Option(None, '--format', help='json 输出单层节点列表')) -> None:
    """展开元数据树(库/表/列逐层懒加载)。"""
    st = get_state(ctx)
    client = st.client()
    row = resolve_conn(client, conn)
    cid: str = row['id']
    if resolve_list_format(fmt) == 'json':
        print_json(_fetch(client, cid, path))
        return
    level_cap = max(1, min(depth, MAX_DEPTH))

    def walk(p: str, level: int) -> None:
        for n in _fetch(client, cid, p):
            mark = '/' if n.get('has_children') else ''
            print(f"{'  ' * level}{n['label']}{mark}")
            if n.get('has_children') and level + 1 < level_cap:
                walk(n['path'], level + 1)

    walk(path, 0)


@handle_cli_error
def ddl(ctx: typer.Context,
        conn: str = typer.Argument(..., metavar='CONN'),
        tables: list[str] = typer.Argument(..., help='表名,可多个')) -> None:
    """打印表 DDL(仅 SQL 类连接)。"""
    client = get_state(ctx).client()
    row = resolve_conn(client, conn)
    # 表名用重复查询参数(?tables=a&tables=b)传,避免逗号拼接把带逗号的表名拆坏;
    # 服务端 /ddl 路由已支持该形式(同时兼容旧的逗号分隔)
    text: str = client.get(f"/api/connections/{row['id']}/ddl", tables=tables)['ddl']
    if not text.strip():
        raise CliError(f'未取到 DDL,确认表名是否正确: {", ".join(tables)}')
    print(text)


@handle_cli_error
def key(ctx: typer.Context,
        conn: str = typer.Argument(..., metavar='CONN'),
        key_name: str = typer.Argument(..., metavar='KEY'),
        db: int = typer.Option(0, '--db', help='Redis 库序号')) -> None:
    """Redis 键详情(TYPE/PTTL/按类型取值)。"""
    client = get_state(ctx).client()
    row = resolve_conn(client, conn)
    print_json(client.get(f"/api/connections/{row['id']}/key", key=key_name, db=db))
