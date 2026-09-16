"""AI 服务:OpenAI 兼容协议的 Text-to-SQL。

只依赖 Chat Completions 协议(POST {base_url}/chat/completions),
DeepSeek / OpenAI / 通义 / 本地 Ollama 等任何兼容服务均可接入。

工具模式:携带 tools 发起流式请求,按 index 累积 delta.tool_calls 分片,
模型发起工具调用时执行(查表结构),结果回放后继续下一轮,直到产出最终回答。
provider 不支持 tools 时透明降级为无工具的单轮流式。
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from .ai_tools import ToolResult

log = logging.getLogger('dbshooter.ai')

DIALECTS = {'sqlite': 'SQLite', 'mysql': 'MySQL 8', 'pg': 'PostgreSQL 15'}

SYSTEM_PROMPT = """你是一个 {dialect} SQL 专家。根据用户给出的表结构和自然语言问题,生成一条可执行的 {dialect} 方言 SQL。

规则:
1. 只输出一条 SELECT 语句,禁止 INSERT/UPDATE/DELETE/DDL。
2. 用 ```sql 代码块包裹 SQL,代码块外可用一句话简述思路。
3. 严格使用给定表结构中存在的表和列,不要臆造。
4. 注意 {dialect} 方言差异(时间函数、LIMIT 等)。
5. 默认加 LIMIT 200 防止全表扫描。"""

TOOLS_PROMPT_EXTRA = """
6. 你可以使用 list_tables / describe_table 工具自助查看当前连接的库表结构;生成 SQL 前必须先确认相关表结构,不要臆造表和列。
7. PostgreSQL 中表可能位于非 public schema,SQL 里写 schema.table 限定名,describe_table 同样传 schema.table。"""

MAX_TOOL_ROUNDS = 6      # 工具调用轮次上限,防止模型死循环


def build_messages(question: str, dialect: str,
                   tools_available: bool = False) -> list[dict[str, Any]]:
    system = SYSTEM_PROMPT.format(dialect=dialect) + (TOOLS_PROMPT_EXTRA if tools_available else '')
    hint = ('请先用 list_tables / describe_table 工具查看当前数据库的表结构。' if tools_available
            else '未提供表结构,请根据问题合理假设。')
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': f'{hint}\n\n问题:{question}'},
    ]


def extract_sql(text: str) -> str | None:
    """从回答中提取 SQL 代码块;无代码块且整体像 SQL 则整体返回。"""
    m = re.search(r'```(?:sql)?\s*\n(.*?)```', text, re.S | re.I)
    if m:
        return m.group(1).strip()
    head = text.strip().split(None, 1)[0].lower() if text.strip() else ''
    return text.strip() if head in ('select', 'with') else None


async def stream_chat(provider: dict[str, Any],
                      messages: list[dict[str, Any]]) -> AsyncIterator[str]:
    """流式调用 OpenAI 兼容接口,逐 token 产出文本。"""
    base = provider['base_url'].rstrip('/')
    headers = {'Content-Type': 'application/json'}
    if provider.get('api_key'):
        headers['Authorization'] = f"Bearer {provider['api_key']}"
    payload = {'model': provider['model'], 'messages': messages, 'stream': True}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as client:
        async with client.stream('POST', f'{base}/chat/completions',
                                 json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    delta = json.loads(data)['choices'][0]['delta'].get('content')
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta


async def complete(provider: dict[str, Any], messages: list[dict[str, str]]) -> str:
    """非流式便捷封装(测试与小结果用)。"""
    parts = [chunk async for chunk in stream_chat(provider, messages)]
    return ''.join(parts)


@dataclass
class ToolCallReq:
    """一次工具调用请求。arguments 保留原始 JSON 字符串,便于原样回放给模型。"""
    id: str
    name: str
    arguments: str


class ToolCallAcc:
    """流式 delta.tool_calls 累积器:按 index 拼接 id/name/arguments 分片。

    OpenAI 流式协议中,id 与 function.name 只出现在该 index 的首个分片,
    后续分片仅携带 arguments 的 JSON 字符串片段;并行调用按 index 区分。
    """

    def __init__(self) -> None:
        self._slots: dict[int, dict[str, str]] = {}

    def apply(self, delta_tcs: list[dict[str, Any]]) -> None:
        for tc in delta_tcs:
            idx = int(tc.get('index') or 0)
            slot = self._slots.setdefault(idx, {'id': '', 'name': '', 'arguments': ''})
            if tc.get('id'):
                slot['id'] += str(tc['id'])
            fn = tc.get('function') or {}
            if fn.get('name'):
                slot['name'] += str(fn['name'])
            if fn.get('arguments'):
                slot['arguments'] += str(fn['arguments'])

    def done(self) -> list[ToolCallReq]:
        return [ToolCallReq(id=s['id'] or f'call_{i}', name=s['name'], arguments=s['arguments'])
                for i, s in sorted(self._slots.items()) if s['name']]


async def stream_round(provider: dict[str, Any],
                       messages: list[dict[str, Any]],
                       tools: list[dict[str, Any]] | None = None,
                       on_token: Callable[[str], Awaitable[None]] | None = None,
                       ) -> tuple[str, list[ToolCallReq]]:
    """单轮流式对话:content 经 on_token 实时转发,tool_calls 累积到流结束返回。

    是否工具轮以"累积到非空 tool_calls"判定,不只信 finish_reason
    (部分兼容服务/代理不打 'tool_calls' 标记)。payload 只加 tools,
    不传 tool_choice/parallel_tool_calls(部分服务对未知字段 400)。
    """
    base = provider['base_url'].rstrip('/')
    headers = {'Content-Type': 'application/json'}
    if provider.get('api_key'):
        headers['Authorization'] = f"Bearer {provider['api_key']}"
    payload: dict[str, Any] = {'model': provider['model'], 'messages': messages, 'stream': True}
    if tools:
        payload['tools'] = tools
    parts: list[str] = []
    acc = ToolCallAcc()
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as client:
        async with client.stream('POST', f'{base}/chat/completions',
                                 json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    delta = json.loads(data)['choices'][0]['delta']
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                content = delta.get('content')
                if content:
                    parts.append(content)
                    if on_token:
                        await on_token(content)
                tcs = delta.get('tool_calls')
                if tcs:
                    acc.apply(tcs)
    return ''.join(parts), acc.done()


async def run_agent(provider: dict[str, Any],
                    messages: list[dict[str, Any]],
                    *,
                    tools: list[dict[str, Any]],
                    run_tool: Callable[[str, dict[str, Any]], Awaitable[ToolResult]],
                    on_token: Callable[[str], Awaitable[None]],
                    on_tool: Callable[[dict[str, Any]], Awaitable[None]],
                    ) -> tuple[str, list[dict[str, Any]]]:
    """工具调用循环:模型自助查表后产出最终回答,返回 (全文, 工具轨迹)。

    轨迹元素 {call_id, name, args, status, summary},随 ai.done 落库与回放。
    降级:首轮 4xx(provider 不支持 tools)且尚未产出 token 时,回退到无工具的
    stream_chat 旧流式;流中途失败不降级(重试会重复 token),直接抛出。
    """
    trace: list[dict[str, Any]] = []
    emitted = False   # 是否已向外吐过 token

    async def forward(t: str) -> None:
        nonlocal emitted
        emitted = True
        await on_token(t)

    for round_no in range(MAX_TOOL_ROUNDS + 1):
        # 最后一轮不再提供 tools,强制模型用已收集的信息收尾
        use_tools = tools if round_no < MAX_TOOL_ROUNDS else None
        try:
            text, calls = await stream_round(provider, messages, use_tools, forward)
        except httpx.HTTPStatusError as e:
            if round_no == 0 and not emitted and e.response.status_code < 500:
                log.info('provider 拒绝 tools(HTTP %s),降级为无工具模式',
                         e.response.status_code)
                parts: list[str] = []
                async for t in stream_chat(provider, messages):
                    parts.append(t)
                    await forward(t)
                return ''.join(parts), trace
            raise
        if not calls:
            return text, trace
        # 回放 assistant 的 tool_calls(arguments 必须是 JSON 字符串),一轮内的
        # 调用全部执行完再发下一轮,否则 OpenAI 系会 400(tool_call_id 无对应 tool 消息)
        messages.append({'role': 'assistant', 'content': text or None,
                         'tool_calls': [{'id': c.id, 'type': 'function',
                                         'function': {'name': c.name,
                                                      'arguments': c.arguments or '{}'}}
                                        for c in calls]})
        for c in calls:
            await on_tool({'call_id': c.id, 'name': c.name,
                           'args': c.arguments, 'status': 'running'})
            try:
                args: object = json.loads(c.arguments) if c.arguments.strip() else {}
                if not isinstance(args, dict):
                    raise ValueError('参数不是 JSON 对象')
            except Exception as e:
                res = ToolResult(f'[错误] 参数解析失败: {e}', '参数解析失败', ok=False)
            else:
                try:
                    res = await run_tool(c.name, args)
                except Exception as e:
                    res = ToolResult(f'[错误] 工具执行异常: {e}', '执行异常', ok=False)
            item = {'call_id': c.id, 'name': c.name, 'args': c.arguments,
                    'status': 'done' if res.ok else 'error', 'summary': res.summary}
            trace.append(item)
            await on_tool(item)
            messages.append({'role': 'tool', 'tool_call_id': c.id, 'content': res.content})
    return '', trace   # 理论不可达:末轮 tools=None,循环内必然返回


async def test_provider(provider: dict[str, Any]) -> tuple[bool, str]:
    """连通性:GET {base_url}/models(多数兼容服务支持)。"""
    base = provider['base_url'].rstrip('/')
    headers = {}
    if provider.get('api_key'):
        headers['Authorization'] = f"Bearer {provider['api_key']}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
            resp = await client.get(f'{base}/models', headers=headers)
            resp.raise_for_status()
        return True, f'连通成功 (HTTP {resp.status_code})'
    except Exception as e:
        return False, f'连接失败: {e}'
