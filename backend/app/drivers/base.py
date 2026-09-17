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


def first_keyword(stmt: str) -> str:
    """剥掉前导注释/括号/空白后取首个关键字(小写);纯注释语句返回 ''。

    旧实现 lstrip(' \\t\\r\\n(-') 把任意个 '-' 当空白剥掉:
    '-- select\\nDELETE ...' 会被判成 select 放行(绕过),而 '-- 注释\\nSELECT ...'
    又会被误判成非查询拦截(误伤)。
    注意 MySQL 可执行版本注释 /*! ... */(及 MariaDB /*M! ... */)不能当注释剥掉,
    否则 '/*! SET ... */' 会绕过白名单 —— 遇 /*! 直接停,首词必然不在白名单内。
    """
    s = stmt
    while True:
        s = s.lstrip(' \t\r\n(')
        if s.startswith('--'):
            nl = s.find('\n')
            s = '' if nl < 0 else s[nl + 1:]
            continue
        if s.startswith('#'):   # MySQL 行注释
            nl = s.find('\n')
            s = '' if nl < 0 else s[nl + 1:]
            continue
        if s.startswith('/*') and not s.startswith('/*!') and not s.startswith('/*M!'):
            end = s.find('*/')
            s = '' if end < 0 else s[end + 2:]
            continue
        break
    parts = s.split(None, 1)
    if not parts:
        return ''
    token = parts[0].lower().rstrip(';')
    # 剥掉分号后为空(如 '; DROP ...'):返回原始 token 让白名单拒绝,
    # 不能当成"纯注释空语句"放行
    return token if token else parts[0].lower()


def ensure_writable(stmt: str, readonly: bool) -> None:
    """只读连接拦截写操作(剥注释后的首词白名单,给出友好提示)。

    仅靠首词判断不够:WITH 数据修改 CTE、EXPLAIN ANALYZE <DML>、写型 PRAGMA
    首词都在 READONLY_PREFIXES 里。真正的硬保证由各驱动在连接层实现
    (SQLite mode=ro / PG default_transaction_read_only / MySQL 会话只读),
    本函数的职责是尽早拦下明确的写操作并给出可读提示。
    """
    if not readonly:
        return
    first = first_keyword(stmt)
    if not first:
        return  # 纯注释/空语句,无可执行内容(也避免 ''.split()[0] 的 IndexError)
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

    async def ai_namespaces(self) -> list[str]:
        """AI 自助查表:命名空间列表(pg=schema,mysql=库,sqlite=['main'])。空=不支持。"""
        return []

    async def ai_tables(self, namespace: str | None = None) -> list[MetaNode]:
        """AI 自助查表:列某命名空间下的表/视图节点(namespace=None 时取默认范围)。"""
        return []

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
