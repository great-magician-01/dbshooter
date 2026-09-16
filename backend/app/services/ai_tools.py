"""AI 自助查表工具:OpenAI tools schema + 只读元数据执行(驱动无关)。

text2sql 场景下,模型通过 list_tables / describe_table 自主查看当前连接的
库表结构,再生成 SQL。所有异常收敛为 ToolResult(ok=False) 错误文本返回给模型,
不向调用方抛出 —— 让模型有机会根据错误自愈重试。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..drivers.base import DriverBase, MetaNode

# 模型入参的标识符白名单(允许 库.表 / schema.表 至多三段;
# mysql ddl 是 SHOW CREATE TABLE {t} f-string 拼接,必须防注入)
_IDENT = re.compile(r'^[A-Za-z0-9_$一-鿿]+(\.[A-Za-z0-9_$一-鿿]+){0,2}$')

MAX_LIST_TABLES = 300    # list_tables 输出的表名总量上限(保护模型上下文)
MAX_DDL_CHARS = 8000     # describe_table 输出的字符上限


@dataclass
class ToolResult:
    content: str          # 给模型的文本(错误时以 [错误] 开头)
    summary: str          # 给 UI 的一句话摘要
    ok: bool = True


def tool_specs(kind: str) -> list[dict[str, Any]]:
    """OpenAI 兼容 tools 定义;description 按方言微调(PG 强调 schema 限定名)。"""
    pg = kind == 'pg'
    schema_desc = ('PostgreSQL 的 schema 名,如 public、shop;不传则按 schema 分组列出全部' if pg
                   else '命名空间(PostgreSQL=schema,MySQL=库名);不传则列出默认范围')
    table_desc = ('表名,PostgreSQL 必须带 schema 前缀,如 shop.users' if pg
                  else '表名,可带库前缀,如 shop.users')
    return [
        {'type': 'function', 'function': {
            'name': 'list_tables',
            'description': '列出当前数据库连接中的表/视图,用于找到与问题相关的表',
            'parameters': {'type': 'object',
                           'properties': {'schema': {'type': 'string', 'description': schema_desc}},
                           'required': []}}},
        {'type': 'function', 'function': {
            'name': 'describe_table',
            'description': '查看一张表的建表结构(列名/类型/约束);生成 SQL 前必须先确认相关表结构',
            'parameters': {'type': 'object',
                           'properties': {'table': {'type': 'string', 'description': table_desc}},
                           'required': ['table']}}},
    ]


async def run_tool(driver: DriverBase, name: str, args: dict[str, Any]) -> ToolResult:
    """工具分发入口;异常收敛为 ToolResult(ok=False)。"""
    try:
        if name == 'list_tables':
            schema = args.get('schema')
            return await _list_tables(driver, str(schema).strip() if schema else None)
        if name == 'describe_table':
            return await _describe_table(driver, str(args.get('table') or ''))
        return ToolResult(f'[错误] 未知工具: {name}', f'未知工具 {name}', ok=False)
    except Exception as e:
        return ToolResult(f'[错误] 工具执行失败: {e}', '执行失败', ok=False)


def _names(nodes: list[MetaNode]) -> list[str]:
    """表节点 → 名字列表,视图带 (view) 后缀提示模型。"""
    return [f'{n.label} (view)' if n.kind == 'view' else n.label for n in nodes]


async def _list_tables(driver: DriverBase, namespace: str | None) -> ToolResult:
    if namespace:
        names = _names(await driver.ai_tables(namespace))
        body = ', '.join(names) or '(该命名空间下无表/视图)'
        return ToolResult(f'{namespace} ({len(names)} 张): {body}', f'列出 {namespace} 下 {len(names)} 张表')

    namespaces = await driver.ai_namespaces()
    if not namespaces:
        return ToolResult('[错误] 当前连接不支持枚举命名空间', '命名空间不可用', ok=False)
    if len(namespaces) == 1:
        names = _names(await driver.ai_tables(namespaces[0]))
        body = ', '.join(names) or '(无表/视图)'
        return ToolResult(f'{namespaces[0]} ({len(names)} 张): {body}', f'列出 {len(names)} 张表')

    # 多命名空间(pg 多 schema / mysql 未绑库):分组列出,总量截断
    lines: list[str] = []
    total = 0
    truncated = False
    for ns in namespaces:
        names = _names(await driver.ai_tables(ns))
        room = max(MAX_LIST_TABLES - total, 0)
        show, total = names[:room], total + min(len(names), room)
        suffix = f', …(共 {len(names)} 张,已截断)' if len(show) < len(names) else ''
        lines.append(f'{ns} ({len(names)} 张): ' + (', '.join(show) or '(无表)') + suffix)
        if total >= MAX_LIST_TABLES:
            truncated = True
            break
    if truncated:
        lines.append('…更多命名空间省略,请用 schema 参数缩小范围')
    return ToolResult('\n'.join(lines), f'列出 {len(namespaces)} 个命名空间的表')


async def _describe_table(driver: DriverBase, table: str) -> ToolResult:
    table = table.strip().strip('`"')
    if not table or not _IDENT.match(table):
        return ToolResult(f'[错误] 非法表名: {table!r},应为 表名 或 命名空间.表名',
                          f'非法表名 {table}', ok=False)
    ddl = await driver.ddl([table])
    tried = table
    if not ddl and driver.kind == 'pg' and '.' not in table:
        # 裸表名缺省查 public 容易落空:在全部 schema 中找唯一同名表再重试
        hits = [ns for ns in await driver.ai_namespaces()
                if any(n.label == table for n in await driver.ai_tables(ns))]
        if len(hits) == 1:
            tried = f'{hits[0]}.{table}'
            ddl = await driver.ddl([tried])
        elif len(hits) > 1:
            cands = ', '.join(f'{ns}.{table}' for ns in hits)
            return ToolResult(f'[错误] 表 {table} 在多个 schema 下同名,请改用限定名之一: {cands}',
                              f'{table} 有多处同名', ok=False)
    if not ddl:
        return ToolResult(f'[错误] 未找到表 {tried},请先用 list_tables 确认表名与所在命名空间',
                          f'未找到 {tried}', ok=False)
    if len(ddl) > MAX_DDL_CHARS:
        ddl = ddl[:MAX_DDL_CHARS] + '\n-- …(截断)'
    return ToolResult(ddl, f'查看 {tried} 表结构')
