"""查询执行 / CSV 导出 / 查询历史。

主通道是 REST 同步执行(POST /api/query/execute,整包返回);
大结果集走 /api/query/export 流式落盘,不经内存。
"""
from __future__ import annotations

import sys
from typing import Any

import typer

from ..errors import CliError, handle_cli_error
from ..output import (FMT_CHOICES, make_console, print_json, print_results,
                      print_rows, resolve_format, result_to_csv)
from ..resolve import resolve_conn
from ..state import get_state

_FMT_OPT = typer.Option(None, '--format', help=f'输出格式: {"/".join(FMT_CHOICES)}')


def _read_stmt(stmt: str | None, file: str | None, use_stdin: bool) -> str:
    """语句三选一:位置参数 / -f 文件 / --stdin 管道。"""
    if sum([stmt is not None, file is not None, use_stdin]) > 1:
        raise CliError('语句只能三选一:位置参数 / -f 文件 / --stdin')
    if file is not None:
        with open(file, encoding='utf-8') as f:
            return f.read()
    if use_stdin:
        return sys.stdin.read()
    if not stmt or not stmt.strip():
        raise CliError('缺少语句:直接跟在 CONN 后,或用 -f 文件 / --stdin 管道')
    return stmt


def _first_rows(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((r for r in results
                 if r.get('kind') in ('rows', 'documents') and not r.get('error')), None)


@handle_cli_error
def query(ctx: typer.Context,
          conn: str = typer.Argument(..., metavar='CONN', help='连接 id / id 前缀 / 名称'),
          stmt: str | None = typer.Argument(None, help='语句(SQL / Mongo JSON 查询 / Redis 命令,按连接类型)'),
          file: str | None = typer.Option(None, '--file', '-f', help='从文件读语句'),
          use_stdin: bool = typer.Option(False, '--stdin', help='从标准输入读语句'),
          limit: int = typer.Option(500, '--limit'),
          schema: str | None = typer.Option(None, '--schema', help='命名空间(目前仅 PG 生效)'),
          fmt: str | None = _FMT_OPT,
          out: str | None = typer.Option(None, '--out', '-o', help='结果写成 CSV 文件')) -> None:
    """执行查询。例:dbs query 本地库 "select * from users" / cat a.sql | dbs query 本地库 --stdin"""
    st = get_state(ctx)
    client = st.client()
    row = resolve_conn(client, conn)
    text = _read_stmt(stmt, file, use_stdin)
    r = client.post('/api/query/execute', {'conn_id': row['id'], 'stmt': text,
                                           'limit': limit, 'schema': schema})
    results: list[dict[str, Any]] = r['results']
    if out is not None:
        first = _first_rows(results)
        if first is None:
            err = next((x.get('error') for x in results if x.get('error')), '查询无结果集')
            raise CliError(str(err))
        with open(out, 'w', encoding='utf-8-sig', newline='') as f:  # 与 /api/query/export 同编码约定
            f.write(result_to_csv(first))
        typer.echo(f"已导出 {len(first.get('rows', []))} 行 → {out}")
        return
    errors = print_results(results, resolve_format(fmt), make_console(st.no_color))
    if errors:
        raise CliError(errors[0])


@handle_cli_error
def export(ctx: typer.Context,
           conn: str = typer.Argument(..., metavar='CONN'),
           stmt: str = typer.Argument(..., help='查询语句'),
           out: str = typer.Option('export.csv', '--out', '-o', help='输出文件'),
           limit: int = typer.Option(10000, '--limit')) -> None:
    """大结果集导出 CSV(服务端流式生成,本地分块落盘)。"""
    client = get_state(ctx).client()
    row = resolve_conn(client, conn)
    n = 0
    with client.stream('/api/query/export',
                       {'conn_id': row['id'], 'stmt': stmt, 'limit': limit}) as resp:
        with open(out, 'wb') as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
                n += len(chunk)
    typer.echo(f'已导出 → {out} ({n} 字节)')


@handle_cli_error
def history(ctx: typer.Context,
            limit: int = typer.Option(50, '--limit'),
            fmt: str | None = _FMT_OPT) -> None:
    """查询历史(服务端记录,与 Web 端共享)。"""
    st = get_state(ctx)
    client = st.client()
    items: list[dict[str, Any]] = client.get('/api/query/history', limit=limit)['items']
    if resolve_format(fmt) == 'json':
        print_json(items)
        return
    conns = {c['id']: c['name'] for c in client.get('/api/connections')['items']}
    print_rows(['时间(UTC)', '连接', '耗时', '行数', '状态', '语句'],
               [[str(h['executed_at']).replace('T', ' '),
                 conns.get(h.get('connection_id'), h.get('connection_id') or ''),
                 f"{h['elapsed_ms']}ms", h['row_count'], h['status'], _short(h['stmt'])]
                for h in items],
               make_console(st.no_color))


def _short(stmt: str, width: int = 60) -> str:
    """语句摘要:压缩空白,超长截断。"""
    s = ' '.join(str(stmt).split())
    return s if len(s) <= width else s[:width - 1] + '…'
