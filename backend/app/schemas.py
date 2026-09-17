"""API 入参模型(全部走 GET/POST,写操作 POST action 风格)。"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

# REST 通道结果行数上限(WS 主通道固定 BUFFER_CAP;REST 整包返回,必须设防)
EXECUTE_LIMIT_MAX = 5000
EXPORT_LIMIT_MAX = 50000


class ConnectionIn(BaseModel):
    """连接入参。update 时可选字段缺省(None)= 不修改,显式传值才覆盖。"""
    id: str | None = None
    name: str
    type: str                      # sqlite | mysql | pg | redis | mongo
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str = ''             # 明文进,加密存;update 时留空 = 不修改
    params: dict[str, Any] | None = None   # sqlite.path / redis.db / mongo.uri 等
    readonly: bool | None = None


class ConnTestIn(BaseModel):
    """测试连接:可传已保存的 id,也可传完整临时配置。"""
    id: str | None = None
    config: ConnectionIn | None = None


class IdIn(BaseModel):
    id: str


class ExecuteIn(BaseModel):
    conn_id: str
    stmt: str
    limit: int = Field(500, ge=1, le=EXECUTE_LIMIT_MAX)
    # 页签绑定的命名空间(目前仅 PG 生效);字段名避开 BaseModel.schema(),入参键仍为 schema
    schema_: str | None = Field(default=None, alias='schema')


class CancelIn(BaseModel):
    query_id: str


class ExportIn(BaseModel):
    conn_id: str
    stmt: str
    limit: int = Field(10000, ge=1, le=EXPORT_LIMIT_MAX)
    # 与 ExecuteIn 同:页签绑定的命名空间(仅 PG 生效)
    schema_: str | None = Field(default=None, alias='schema')


class TabIn(BaseModel):
    id: str
    type: str                      # sql | data | redis | mongo
    title: str
    connection_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
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
    values: dict[str, Any]

    @field_validator('values')
    @classmethod
    def _check_values(cls, v: dict[str, Any]) -> dict[str, Any]:
        """settings 是任意键值存储,收窄写入面:键白名单字符 + 数量/长度上限。"""
        if len(v) > 100:
            raise ValueError('settings 项数过多(上限 100)')
        for k, val in v.items():
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', str(k)):
                raise ValueError(f'非法 settings 键: {k!r}(仅限字母数字与 _ . -)')
            if len(str(val)) > 10000:
                raise ValueError(f'settings 值过长(上限 10000 字符): {k}')
        return v
