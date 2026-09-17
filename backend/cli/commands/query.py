"""查询执行 / CSV 导出 / 查询历史。

主通道是 REST 同步执行(POST /api/query/execute,整包返回);
大结果集走 /api/query/export 流式落盘,不经内存。
"""
from __future__ import annotations

import sys
from typing import Any

import typer

from ..errors import CliError, handle_cli_error
from ..output import (FMT_CHOICES, data_results, make_console, print_json, print_results,
                      print_rows, resolve_format, resolve_list_format, warn, write_results_csv)
from ..resolve import resolve_conn
from ..state import get_state

_FMT_OPT = typer.Option(None, '--format', help=f'输出格式: {"/".join(FMT_CHOICES)}')

# 与服务端 /api/query/execute、/api/query/export 的 limit 校验保持一致,提前本地报错
QUERY_LIMIT_MAX = 5000
EXPORT_LIMIT_MAX = 50000
# 服务端 GET /api/query/history 是 min(limit, 500):超了只会静默截断,本地先拦
HISTORY_LIMIT_MAX = 500


def _read_stdin() -> str:
    """读管道语句:utf-8 严格解码,失败回退 GBK(与 _read_stmt_file 同一口径)。

    必须绕到 buffer 拿原始字节:main._fix_stdio 已把 stdin 重配成 utf-8 + errors=replace,
    文本层读 GBK 内容只会得到替换字符,永远退化不回 gbk。
    管道带 BOM(Windows "UTF-8 with BOM" 存盘/重定向)时首词会带上 BOM(﻿),变成
    ﻿SELECT,服务端只读拦截按首词判断会误判 → 与 _read_stmt_file 的 utf-8-sig 对齐,剥掉。
    """
    buf = getattr(sys.stdin, 'buffer', None)
    if buf is None:
        # 测试/嵌入式场景替换过 stdin 且没有字节层:退回文本读取(仍剥 BOM)
        return sys.stdin.read().lstrip('﻿')
    raw: bytes = buf.read()
    for enc in ('utf-8', 'gbk'):
        try:
            return raw.decode(enc).lstrip('﻿')
        except UnicodeDecodeError:
            continue
    raise CliError('无法识别标准输入编码(支持 UTF-8/GBK)')


def _read_stmt(stmt: str | None, file: str | None, use_stdin: bool) -> str:
    """语句三选一:位置参数 / -f 文件 / --stdin 管道。"""
    if sum([stmt is not None, file is not None, use_stdin]) > 1:
        raise CliError('语句只能三选一:位置参数 / -f 文件 / --stdin')
    if file is not None:
        return _read_stmt_file(file)
    if use_stdin:
        return _read_stdin()
    if not stmt or not stmt.strip():
        raise CliError('缺少语句:直接跟在 CONN 后,或用 -f 文件 / --stdin 管道')
    return stmt


def _check_limit(limit: int, max_: int, note: str = '') -> int:
    """本地校验 limit 上限:超限直接给中文报错,不要等服务端 422 或静默截断。"""
    if not 1 <= limit <= max_:
        raise CliError(f'--limit 需在 1~{max_} 之间: {limit}{note}')
    return limit


def _read_stmt_file(file: str) -> str:
    """读语句文件:utf-8-sig 剥 BOM(BOM 会让只读拦截误判首词),GBK 兜底(中文 Windows 常见)。"""
    with open(file, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8-sig', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise CliError(f'无法识别文件编码(支持 UTF-8/GBK): {file}')


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
                                           'limit': _check_limit(limit, QUERY_LIMIT_MAX),
                                           'schema': schema})
    results: list[dict[str, Any]] = r['results']
    if out is not None:
        # 与 --format csv 同一渲染口径:全部含数据的结果集都落盘(含 redis command 结果)
        if not data_results(results):
            err = next((x.get('error') for x in results if x.get('error')), None)
            if err:
                raise CliError(str(err))
            # 纯写语句(全部 affected)没有结果集:仍要把文件写空(而不是不写)——
            # 否则 `dbs query … -o out.csv && cat out.csv` 会读到上一次留下的陈旧数据。
            # 提示走 stderr,退出码 0(语句本身执行成功,不算失败)
            aff = [str(x['affected']) for x in results
                   if x.get('kind') == 'affected' and x.get('affected') is not None]
            extra = f"(影响行数: {', '.join(aff)})" if aff else ''
            with open(out, 'w', encoding='utf-8-sig', newline=''):   # 仅截断:0 字节
                pass
            warn(f'无结果集,已写出空文件{extra}: {out}')
            return
        with open(out, 'w', encoding='utf-8-sig', newline='') as f:  # BOM:Excel 直开不乱码
            n = write_results_csv(results, f)
        typer.echo(f'已导出 {n} 行 → {out}')
        return
    errors = print_results(results, resolve_format(fmt), make_console(st.no_color))
    if errors:
        raise CliError(errors[0])


@handle_cli_error
def export(ctx: typer.Context,
           conn: str = typer.Argument(..., metavar='CONN'),
           stmt: str | None = typer.Argument(None, help='查询语句(或 -f / --stdin)'),
           file: str | None = typer.Option(None, '--file', '-f', help='从文件读语句'),
           use_stdin: bool = typer.Option(False, '--stdin', help='从标准输入读语句'),
           out: str = typer.Option('export.csv', '--out', '-o', help='输出文件'),
           limit: int = typer.Option(10000, '--limit'),
           schema: str | None = typer.Option(None, '--schema', help='命名空间(目前仅 PG 生效)')) -> None:
    """大结果集导出 CSV(服务端流式生成,本地分块落盘)。

    文件名由 -o 指定,不解析服务端的 Content-Disposition(本地自命名更可控)。"""
    client = get_state(ctx).client()
    row = resolve_conn(client, conn)
    text = _read_stmt(stmt, file, use_stdin)
    n = 0
    with client.stream('/api/query/export',
                       {'conn_id': row['id'], 'stmt': text, 'schema': schema,
                        'limit': _check_limit(limit, EXPORT_LIMIT_MAX)}) as resp:
        # 服务端流是纯 UTF-8 无 BOM,本地补 BOM,与 query -o 行为一致(Excel 直开不乱码)
        with open(out, 'wb') as f:
            f.write(b'\xef\xbb\xbf')
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
    items: list[dict[str, Any]] = client.get(
        '/api/query/history',
        limit=_check_limit(limit, HISTORY_LIMIT_MAX,
                           f'(history 服务端上限 {HISTORY_LIMIT_MAX},超出会被静默截断)'))['items']
    if resolve_list_format(fmt) == 'json':
        print_json(items)
        return
    conns = {c['id']: c['name'] for c in client.get('/api/connections')['items']}
    print_rows(['时间(UTC)', '连接', '耗时', '行数', '状态', '语句'],
               [[str(h['executed_at'])[:19].replace('T', ' '),   # 截到秒,避免时区后缀挤爆列宽
                 conns.get(h.get('connection_id'), h.get('connection_id') or ''),
                 f"{h['elapsed_ms']}ms", h['row_count'], h['status'], _short(h['stmt'])]
                for h in items],
               make_console(st.no_color))


def _short(stmt: str, width: int = 60) -> str:
    """语句摘要:压缩空白,超长截断。"""
    s = ' '.join(str(stmt).split())
    return s if len(s) <= width else s[:width - 1] + '…'
