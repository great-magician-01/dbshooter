"""连接引用解析:精确 id > 精确名称 > 唯一 id 前缀 > 唯一名称前缀;歧义时报错并列候选。"""
from __future__ import annotations

from typing import Any

from .client import ApiClient
from .errors import CliError


def _line(c: dict[str, Any]) -> str:
    return f"  {str(c['id'])[:8]}  {c['name']} ({c['type']})"


def _ambiguous(ref: str, candidates: list[dict[str, Any]]) -> CliError:
    lines = '\n'.join(_line(c) for c in candidates)
    return CliError(f'连接 "{ref}" 匹配到多个,请用完整 id 或更长的前缀:\n{lines}')


def resolve_conn(client: ApiClient, ref: str) -> dict[str, Any]:
    """把用户输入的连接引用(id / id 前缀 / 名称 / 名称前缀)解析为连接记录。

    优先级:精确 id > 精确名称 > 唯一 id 前缀 > 唯一名称前缀。
    精确匹配压过前缀:名称恰好是另一连接 id 前缀时,输入的名字必须解析到同名连接上。
    """
    if not ref.strip():
        # 空 ref 会让 startswith('') 恒真:单连接时静默落到唯一连接上(危险)
        raise CliError('请提供连接 id / 前缀 / 名称')
    items: list[dict[str, Any]] = client.get('/api/connections')['items']
    exact_id = [c for c in items if str(c['id']) == ref]
    if exact_id:
        return exact_id[0]
    exact_name = [c for c in items if c['name'] == ref]
    if len(exact_name) == 1:
        return exact_name[0]
    id_prefix = [c for c in items if str(c['id']).startswith(ref)]
    if len(id_prefix) == 1:
        return id_prefix[0]
    name_prefix = [c for c in items if str(c['name']).startswith(ref)]
    if len(name_prefix) == 1:
        return name_prefix[0]
    # 走到这里:要么一个都没匹配上,要么匹配到多组。重名(exact_name>1)同样算歧义
    for candidates in (id_prefix, name_prefix, exact_name):
        if len(candidates) > 1:
            raise _ambiguous(ref, candidates)
    raise CliError(f'找不到连接: {ref}(用 dbs conn list 查看现有连接)')
