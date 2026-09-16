"""AI 服务:OpenAI 兼容协议的 Text-to-SQL。

只依赖 Chat Completions 协议(POST {base_url}/chat/completions),
DeepSeek / OpenAI / 通义 / 本地 Ollama 等任何兼容服务均可接入。
"""
from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator

import httpx

DIALECTS = {'sqlite': 'SQLite', 'mysql': 'MySQL 8', 'pg': 'PostgreSQL 15'}

SYSTEM_PROMPT = """你是一个 {dialect} SQL 专家。根据用户给出的表结构和自然语言问题,生成一条可执行的 {dialect} 方言 SQL。

规则:
1. 只输出一条 SELECT 语句,禁止 INSERT/UPDATE/DELETE/DDL。
2. 用 ```sql 代码块包裹 SQL,代码块外可用一句话简述思路。
3. 严格使用给定表结构中存在的表和列,不要臆造。
4. 注意 {dialect} 方言差异(时间函数、LIMIT 等)。
5. 默认加 LIMIT 200 防止全表扫描。"""


def build_messages(question: str, ddl: str, dialect: str) -> list[dict[str, str]]:
    return [
        {'role': 'system', 'content': SYSTEM_PROMPT.format(dialect=dialect)},
        {'role': 'user', 'content': f'表结构:\n{ddl or "(未提供,请根据问题合理假设)"}\n\n问题:{question}'},
    ]


def extract_sql(text: str) -> str | None:
    """从回答中提取 SQL 代码块;无代码块且整体像 SQL 则整体返回。"""
    m = re.search(r'```(?:sql)?\s*\n(.*?)```', text, re.S | re.I)
    if m:
        return m.group(1).strip()
    head = text.strip().split(None, 1)[0].lower() if text.strip() else ''
    return text.strip() if head in ('select', 'with') else None


async def stream_chat(provider: dict[str, Any], messages: list[dict[str, str]]) -> AsyncIterator[str]:
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
