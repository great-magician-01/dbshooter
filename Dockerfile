# ── 阶段一:构建前端 ──
FROM node:20-alpine AS fe
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── 阶段二:后端运行时 + 前端产物(单镜像交付) ──
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# 装本包本身(--no-deps:依赖已由 requirements.txt 装好),镜像内才 `dbs` 命令可用,
# 支持 docs/02-CLI设计方案.md 承诺的 docker exec <容器> dbs ...;测试目录已从 wheel 排除
COPY pyproject.toml ./
COPY backend/ ./backend/
RUN pip install --no-cache-dir --no-deps .
COPY run.py ./
COPY --from=fe /fe/dist ./frontend_dist/

ENV DBSHOOTER_DATA_DIR=/data
# run.py 默认只监听 127.0.0.1(本机开发友好);容器里必须显式放开,端口映射才有意义
ENV DBSHOOTER_HOST=0.0.0.0
EXPOSE 5718

# 非 root 运行:先建好数据目录并交给 appuser(否则容器内写不了元数据库与 secret.key)。
# 注意顺序:必须在 VOLUME 之前改 /data —— 声明卷之后再改卷内文件,改动会被丢掉。
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data
VOLUME /data
USER appuser

CMD ["python", "run.py"]
