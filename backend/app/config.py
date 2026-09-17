"""全局配置:数据目录、加密密钥路径、前端产物目录、可选访问令牌。

一切可被环境变量覆盖,便于 Docker 部署与测试隔离。
"""
from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]  # 项目根目录

# pip 安装形态下 _ROOT 落在 site-packages,数据目录不能写进那里,退回当前工作目录
_DEFAULT_DATA = (_ROOT / 'data') if (_ROOT / 'run.py').exists() else (Path.cwd() / 'data')
DATA_DIR = Path(os.environ.get('DBSHOOTER_DATA_DIR', _DEFAULT_DATA))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / 'dbshooter.db'
SECRET_KEY_PATH = DATA_DIR / 'secret.key'
_FRONTEND_CANDIDATES = [
    os.environ.get('DBSHOOTER_FRONTEND'),
    _ROOT / 'frontend_dist',      # Docker 镜像内
    _ROOT / 'frontend' / 'dist',  # 本地构建后
]
FRONTEND_DIST: Path | None = next(
    (Path(c) for c in _FRONTEND_CANDIDATES if c and Path(c).is_dir()), None)

# 可选全局访问令牌;设置后 /api 与 /ws 均需鉴权
ACCESS_TOKEN: str | None = os.environ.get('DBSHOOTER_TOKEN') or None
