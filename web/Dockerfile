FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Web 依赖
COPY web/requirements.txt ./web-requirements.txt
RUN pip install --no-cache-dir -r web-requirements.txt

# 原项目依赖（用于 subprocess 调用 main.py --once）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY web/ ./web/
COPY src/ ./src/
COPY main.py ./

# 配置 Entrypoint
RUN chmod +x web/docker-entrypoint.sh
ENTRYPOINT ["web/docker-entrypoint.sh"]
CMD ["python", "-m", "web.server"]
