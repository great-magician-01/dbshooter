"""驱动抽象层:统一五种数据库的连接、元数据、执行语义。

新增数据库 = 新增一个 driver 文件并 @register,框架零改动。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, TypeVar

# 只读模式下放行的语句首词
READONLY_PREFIXES = ('select', 'with', 'explain', 'show', 'describe', 'desc', 'pragma')


class QueryError(Exception):
    """执行期错误,message 直接展示给用户。"""


class ReadonlyViolation(QueryError):
    pass


@dataclass
class MetaNode:
    path: str                      # 懒加载路径,如 "shop" / "shop.users"
    label: str
    kind: str                      # database|schema|table|view|column|keygroup|key|collection|index
    has_children: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecResult:
    kind: str = 'rows'             # rows | affected | command | documents
    columns: list[dict[str, Any]] = field(default_factory=list)   # [{name, type}]
    rows: list[list[Any]] = field(default_factory=list)
    affected: int | None = None
    elapsed_ms: int = 0
    truncated: bool = False        # 命中 limit,还有更多行
    error: str | None = None
    raw: object = None             # redis 原始回包等

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in {
            'kind': self.kind, 'columns': self.columns, 'rows': self.rows,
            'affected': self.affected, 'elapsed_ms': self.elapsed_ms,
            'truncated': self.truncated, 'error': self.error, 'raw': self.raw,
        }.items() if v is not None}


def ensure_writable(stmt: str, readonly: bool) -> None:
    """只读连接拦截写操作(AI 护栏之外的第二道保险)。"""
    if not readonly:
        return
    first = stmt.lstrip(' \t\r\n(-').split(None, 1)[0].lower() if stmt.strip() else ''
    if first not in READONLY_PREFIXES:
        raise ReadonlyViolation(f'当前连接为只读模式,已拦截非查询语句: {first.upper()}')


class DriverBase(abc.ABC):
    kind: str = ''
    editor_mode: str = 'sql'       # sql | json-query | command

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg

    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def test(self) -> tuple[bool, str]: ...

    @abc.abstractmethod
    async def metadata(self, path: str) -> list[MetaNode]: ...

    @abc.abstractmethod
    async def execute(self, stmt: str, limit: int = 500,
                      schema: str | None = None) -> list[ExecResult]:
        """执行语句。schema = 页签绑定的命名空间(目前仅 PG 实现),不支持的驱动忽略。"""

    async def ddl(self, tables: list[str]) -> str:
        """text2sql 上下文:表结构 DDL(SQL 类驱动实现)。"""
        return ''

    async def cancel(self) -> None:
        """尽力取消;不支持的驱动由上层降级处理。"""

    async def close(self) -> None: ...


REGISTRY: dict[str, type[DriverBase]] = {}

_DriverT = TypeVar('_DriverT', bound=DriverBase)


def register(cls: type[_DriverT]) -> type[_DriverT]:
    """注册驱动。用 TypeVar 保留子类类型,否则 @register 装饰会把类抹平成
    type[DriverBase],上游 isinstance(driver, XxxDriver) 将无法类型收窄。"""
    REGISTRY[cls.kind] = cls
    return cls


def create_driver(cfg: dict[str, Any]) -> DriverBase:
    if cfg['type'] not in REGISTRY:
        raise QueryError(f'不支持的数据库类型: {cfg["type"]}')
    return REGISTRY[cfg['type']](cfg)


def registered_types() -> list[str]:
    return sorted(REGISTRY.keys())
