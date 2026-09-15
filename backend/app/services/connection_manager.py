"""连接运行时管理:按需创建驱动实例并缓存,负责直连配置构建。"""
from __future__ import annotations

import asyncio

from .. import db
from ..drivers import DriverBase, QueryError, create_driver


class ConnectionManager:
    def __init__(self) -> None:
        self._drivers: dict[str, DriverBase] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, conn_id: str) -> DriverBase:
        if conn_id in self._drivers:
            return self._drivers[conn_id]
        lock = self._locks.setdefault(conn_id, asyncio.Lock())
        async with lock:
            if conn_id in self._drivers:
                return self._drivers[conn_id]
            cfg = db.get_connection(conn_id)
            if not cfg:
                raise QueryError(f'连接不存在: {conn_id}')
            driver = create_driver(cfg)
            await driver.connect()
            self._drivers[conn_id] = driver
            return driver

    async def test_config(self, cfg: dict) -> tuple[bool, str]:
        """用完整临时配置试连(不进入缓存)。"""
        driver = create_driver(cfg)
        try:
            return await driver.test()
        finally:
            await driver.close()

    async def evict(self, conn_id: str) -> None:
        driver = self._drivers.pop(conn_id, None)
        if driver:
            await driver.close()
        self._locks.pop(conn_id, None)

    async def close_all(self) -> None:
        for driver in self._drivers.values():
            try:
                await driver.close()
            except Exception:
                pass
        self._drivers.clear()


manager = ConnectionManager()
