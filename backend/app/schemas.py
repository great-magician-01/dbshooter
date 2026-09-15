"""API 入参模型(全部走 GET/POST,写操作 POST action 风格)。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ConnectionIn(BaseModel):
    id: str | None = None
    name: str
    type: str                      # sqlite | mysql | pg | redis | mongo
    host: str = ''
    port: int | None = None
    database: str = ''
    username: str = ''
    password: str = ''             # 明文进,加密存;update 时留空 = 不修改
    params: dict = Field(default_factory=dict)   # sqlite.path / redis.db / mongo.uri 等
    readonly: bool = False


class ConnTestIn(BaseModel):
    """测试连接:可传已保存的 id,也可传完整临时配置。"""
    id: str | None = None
    config: ConnectionIn | None = None


class IdIn(BaseModel):
    id: str


class ExecuteIn(BaseModel):
    conn_id: str
    stmt: str
    limit: int = 500


class PageIn(BaseModel):
    query_id: str


class CancelIn(BaseModel):
    query_id: str


class ExportIn(BaseModel):
    conn_id: str
    stmt: str
    limit: int = 10000


class TabIn(BaseModel):
    id: str
    type: str                      # sql | data | redis | mongo
    title: str
    connection_id: str | None = None
    context: dict = Field(default_factory=dict)
    content: str = ''
    sort: int = 0


class TabOrderIn(BaseModel):
    ids: list[str]
    active_id: str | None = None


class ProviderIn(BaseModel):
    id: str | None = None
    name: str
    base_url: str
    api_key: str = ''              # update 时留空 = 不修改
    model: str


class SessionIn(BaseModel):
    id: str | None = None
    title: str | None = None
    connection_id: str | None = None


class SessionRenameIn(BaseModel):
    id: str
    title: str


class SettingsIn(BaseModel):
    values: dict


class AiAskIn(BaseModel):
    session_id: str
    conn_id: str
    question: str
    tables: list[str] = Field(default_factory=list)
