"""加解密:连接密码 / api_key 落库前的保护。"""
import pytest
from cryptography.fernet import InvalidToken

from backend.app import security


def test_roundtrip():
    token = security.encrypt('my-secret-password')
    assert token and token != 'my-secret-password'
    assert security.decrypt(token) == 'my-secret-password'


def test_empty_passthrough():
    assert security.encrypt('') == ''
    assert security.decrypt('') == ''


def test_tamper_raises():
    with pytest.raises(InvalidToken):
        security.decrypt(security.encrypt('a')[:-4] + 'XXXX')


def test_chinese():
    assert security.decrypt(security.encrypt('密码包含中文')) == '密码包含中文'
