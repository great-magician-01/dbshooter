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
COPY backend/ ./backend/
COPY run.py ./
COPY --from=fe /fe/dist ./frontend_dist/

ENV DBSHOOTER_DATA_DIR=/data
VOLUME /data
EXPOSE 8000

CMD ["python", "run.py"]
