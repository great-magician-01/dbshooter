"""敏感字段加解密(Fernet)。

主密钥来源:
1. 环境变量 DBSHOOTER_SECRET(任意字符串,内部 SHA256 派生)
2. 否则首启自动生成数据目录下 secret.key(权限 0600)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.fernet import Fernet

from . import config

_fernet: Fernet | None = None


def token_matches(provided: str | None) -> bool:
    """访问令牌校验(恒时比较,防时序侧信道)。未启用令牌时恒 False。"""
    tok = config.ACCESS_TOKEN
    return tok is not None and provided is not None and hmac.compare_digest(provided, tok)


def _load_key() -> bytes:
    env = os.environ.get('DBSHOOTER_SECRET')
    if env:
        return base64.urlsafe_b64encode(hashlib.sha256(env.encode()).digest())
    if config.SECRET_KEY_PATH.exists():
        return config.SECRET_KEY_PATH.read_bytes().strip()
    key = Fernet.generate_key()
    config.SECRET_KEY_PATH.write_bytes(key)
    try:
        os.chmod(config.SECRET_KEY_PATH, 0o600)
    except OSError:
        pass  # Windows 上 chmod 语义有限,忽略
    return key


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def encrypt(plain: str) -> str:
    """空串原样返回,便于区分"未设置"。"""
    if not plain:
        return ''
    return fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ''
    return fernet().decrypt(token.encode()).decode()


def reset_for_tests() -> None:
    global _fernet
    _fernet = None
