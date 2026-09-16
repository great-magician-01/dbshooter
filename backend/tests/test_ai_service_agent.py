"""agent 循环与流式 tool_calls 解析:monkeypatch stream_round/stream_chat,不走真实 HTTP。"""
from __future__ import annotations

import json

import httpx
import pytest

from backend.app.services import ai_service
from backend.app.services.ai_tools import ToolResult

PROV = {'base_url': 'http://mock/v1', 'model': 'm', 'api_key': ''}
TOOLS = [{'type': 'function', 'function': {'name': 'list_tables', 'parameters': {}}}]


def _http_err(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request('POST', 'http://mock/v1/chat/completions')
    return httpx.HTTPStatusError(f'status {status}', request=req,
                                 response=httpx.Response(status, request=req))


async def _shift(rounds: list) -> tuple:
    """fake stream_round:每次调用弹出下一轮预设的 (text, tool_calls)。"""
    return rounds.pop(0)


class _Recorder:
    """收集 on_token / on_tool / run_tool 回调。"""

    def __init__(self, tool_result: ToolResult | None = None):
        self.tokens: list[str] = []
        self.tool_evs: list[dict] = []
        self.tool_calls: list[tuple[str, dict]] = []
        self.tool_result = tool_result or ToolResult('main: users', '列出 1 张表')

    async def on_token(self, t: str) -> None:
        self.tokens.append(t)

    async def on_tool(self, ev: dict) -> None:
        self.tool_evs.append(ev)

    async def run_tool(self, name: str, args: dict) -> ToolResult:
        self.tool_calls.append((name, args))
        return self.tool_result


def test_tool_call_acc_fragments():
    """id/name 只在首片出现,arguments 跨片拼接;并行 index 互不串。"""
    acc = ai_service.ToolCallAcc()
    acc.apply([{'index': 0, 'id': 'c1',
                'function': {'name': 'describe_table', 'arguments': '{"tab'}}])
    acc.apply([{'index': 1, 'id': 'c2', 'function': {'name': 'list_tables'}}])
    acc.apply([{'index': 0, 'function': {'arguments': 'le":"users"}'}}])
    acc.apply([{'index': 1, 'function': {'arguments': '{}'}}])
    calls = acc.done()
    assert [c.id for c in calls] == ['c1', 'c2']
    assert calls[0].name == 'describe_table'
    assert calls[0].arguments == '{"table":"users"}'
    assert json.loads(calls[1].arguments) == {}


def test_tool_call_acc_missing_id():
    """部分服务不回 id,用 call_{index} 兜底。"""
    acc = ai_service.ToolCallAcc()
    acc.apply([{'index': 2, 'function': {'name': 'list_tables', 'arguments': '{}'}}])
    calls = acc.done()
    assert len(calls) == 1 and calls[0].id == 'call_2'


async def test_agent_two_rounds(monkeypatch):
    """先调 describe_table 再出 SQL:消息回放结构 + 轨迹 + token 透传。"""
    rounds = [
        ('', [ai_service.ToolCallReq('c1', 'describe_table', '{"table": "users"}')]),
        ('```sql\nSELECT * FROM users LIMIT 200;\n```', []),
    ]

    async def fake_round(provider, messages, tools=None, on_token=None):
        text, calls = rounds.pop(0)
        if on_token and text:
            await on_token(text)
        return text, calls

    monkeypatch.setattr(ai_service, 'stream_round', fake_round)
    rec = _Recorder(ToolResult('CREATE TABLE users(id int);', '查看 users 表结构'))
    messages = ai_service.build_messages('查用户', 'SQLite', tools_available=True)

    text, trace = await ai_service.run_agent(
        PROV, messages, tools=TOOLS, run_tool=rec.run_tool,
        on_token=rec.on_token, on_tool=rec.on_tool)

    assert text.startswith('```sql') and rec.tokens == [text]
    assert rec.tool_calls == [('describe_table', {'table': 'users'})]
    # 轨迹:running 占位 → done 落终态
    assert [e['status'] for e in rec.tool_evs] == ['running', 'done']
    assert rec.tool_evs[0]['call_id'] == 'c1'
    assert trace == [{'call_id': 'c1', 'name': 'describe_table',
                      'args': '{"table": "users"}', 'status': 'done',
                      'summary': '查看 users 表结构'}]
    # 回放:assistant 带完整 tool_calls(arguments 为字符串),tool 消息按 id 对应
    assistant_msg = messages[2]
    assert assistant_msg['role'] == 'assistant'
    assert assistant_msg['tool_calls'][0]['id'] == 'c1'
    assert isinstance(assistant_msg['tool_calls'][0]['function']['arguments'], str)
    assert messages[3] == {'role': 'tool', 'tool_call_id': 'c1',
                           'content': 'CREATE TABLE users(id int);'}


async def test_agent_parallel_tool_calls(monkeypatch):
    """一轮多个 tool_calls 全部执行,两个 tool 消息按序追加。"""
    rounds = [('', [ai_service.ToolCallReq('c1', 'describe_table', '{"table":"a"}'),
                    ai_service.ToolCallReq('c2', 'describe_table', '{"table":"b"}')]),
              ('ok', [])]
    monkeypatch.setattr(ai_service, 'stream_round',
                        lambda *a, **k: _shift(rounds))
    rec = _Recorder()
    text, trace = await ai_service.run_agent(
        PROV, [], tools=TOOLS, run_tool=rec.run_tool,
        on_token=rec.on_token, on_tool=rec.on_tool)
    assert text == 'ok' and len(trace) == 2
    assert [c[1]['table'] for c in rec.tool_calls] == ['a', 'b']


async def test_agent_fallback_on_4xx(monkeypatch):
    """首轮 4xx(provider 不支持 tools)降级到无工具的 stream_chat。"""
    async def fake_round(provider, messages, tools=None, on_token=None):
        raise _http_err(400)

    async def fake_stream(provider, messages):
        yield '无工具回答'

    monkeypatch.setattr(ai_service, 'stream_round', fake_round)
    monkeypatch.setattr(ai_service, 'stream_chat', fake_stream)
    rec = _Recorder()
    text, trace = await ai_service.run_agent(
        PROV, [], tools=TOOLS, run_tool=rec.run_tool,
        on_token=rec.on_token, on_tool=rec.on_tool)
    assert text == '无工具回答' and trace == []
    assert rec.tokens == ['无工具回答'] and rec.tool_calls == []


async def test_agent_no_fallback_on_5xx(monkeypatch):
    async def fake_round(provider, messages, tools=None, on_token=None):
        raise _http_err(500)

    monkeypatch.setattr(ai_service, 'stream_round', fake_round)
    rec = _Recorder()
    with pytest.raises(httpx.HTTPStatusError):
        await ai_service.run_agent(PROV, [], tools=TOOLS, run_tool=rec.run_tool,
                                   on_token=rec.on_token, on_tool=rec.on_tool)


async def test_agent_max_rounds_forces_final(monkeypatch):
    """模型死循环调工具:满 MAX_TOOL_ROUNDS 后以 tools=None 强制收尾。"""
    seen: list[bool] = []

    async def fake_round(provider, messages, tools=None, on_token=None):
        seen.append(tools is not None)
        if tools is None:
            return '最终回答', []
        return '', [ai_service.ToolCallReq('c', 'list_tables', '{}')]

    monkeypatch.setattr(ai_service, 'stream_round', fake_round)
    rec = _Recorder()
    text, trace = await ai_service.run_agent(
        PROV, [], tools=TOOLS, run_tool=rec.run_tool,
        on_token=rec.on_token, on_tool=rec.on_tool)
    assert text == '最终回答'
    assert seen == [True] * ai_service.MAX_TOOL_ROUNDS + [False]
    assert len(trace) == ai_service.MAX_TOOL_ROUNDS


async def test_agent_tool_error_recovers(monkeypatch):
    """工具失败(表不存在)以错误文本进 tool 消息,循环继续出答案。"""
    rounds = [('', [ai_service.ToolCallReq('c1', 'describe_table', '{"table":"nope"}')]),
              ('找不到该表', [])]
    monkeypatch.setattr(ai_service, 'stream_round',
                        lambda *a, **k: _shift(rounds))
    rec = _Recorder(ToolResult('[错误] 未找到表 nope', '未找到 nope', ok=False))
    messages: list[dict] = []
    text, trace = await ai_service.run_agent(
        PROV, messages, tools=TOOLS, run_tool=rec.run_tool,
        on_token=rec.on_token, on_tool=rec.on_tool)
    assert text == '找不到该表'
    assert trace[0]['status'] == 'error'
    tool_msgs = [m for m in messages if m['role'] == 'tool']
    assert tool_msgs[0]['content'].startswith('[错误]')


async def test_agent_bad_arguments(monkeypatch):
    """arguments JSON 损坏:不调用工具,错误文本进 tool 消息让模型重试。"""
    rounds = [('', [ai_service.ToolCallReq('c1', 'describe_table', '{bad json')]),
              ('重试后回答', [])]
    monkeypatch.setattr(ai_service, 'stream_round',
                        lambda *a, **k: _shift(rounds))
    rec = _Recorder()
    messages: list[dict] = []
    text, trace = await ai_service.run_agent(
        PROV, messages, tools=TOOLS, run_tool=rec.run_tool,
        on_token=rec.on_token, on_tool=rec.on_tool)
    assert text == '重试后回答'
    assert rec.tool_calls == []            # 未真正执行
    assert trace[0]['status'] == 'error'
    assert '参数解析失败' in [m for m in messages if m['role'] == 'tool'][0]['content']
