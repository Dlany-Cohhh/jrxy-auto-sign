# 今日校园自动签到 · Web 服务镜像（Linux 服务器一键部署）
# 构建：docker build -t jrxy-sign .
# 运行：docker run -d -p 8000:8000 -v jrxy-sign-data:/app/web-server/data jrxy-sign
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    SIGN_TIME=22:05 \
    AUTO_SIGN=1

WORKDIR /app

# ddddocr/onnxruntime 运行所需系统库
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用构建缓存）
COPY jrxyzuixingnx/requirements.txt /tmp/engine-req.txt
RUN python -m venv /app/jrxyzuixingnx/.venv \
    && /app/jrxyzuixingnx/.venv/bin/pip install --no-cache-dir -q -r /tmp/engine-req.txt

COPY web-server/requirements.txt /tmp/web-req.txt
RUN python -m venv /app/web-server/.venv \
    && /app/web-server/.venv/bin/pip install --no-cache-dir -q -r /tmp/web-req.txt

# 复制引擎与 Web 服务（auto-sign 为已失效原仓库，不进镜像，见 .dockerignore）
COPY jrxyzuixingnx /app/jrxyzuixingnx
COPY web-server /app/web-server

# 运行时数据（签到日志、坐标覆盖配置）持久化
VOLUME ["/app/web-server/data"]

EXPOSE 8000

WORKDIR /app/web-server
CMD [".venv/bin/python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
