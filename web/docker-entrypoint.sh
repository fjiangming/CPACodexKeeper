#!/bin/bash
set -e

DATA_DIR="/app/web/data"
ENV_FILE="${DATA_DIR}/.env"
APP_ENV_FILE="/app/.env"

# 如果数据目录不存在则创建
mkdir -p "$DATA_DIR"

# 如果 .env 不存在于挂载的 volume 中，则自动生成一个默认模板
if [ ! -f "$ENV_FILE" ]; then
    echo "Initializing default .env in volume..."
    cat <<EOF > "$ENV_FILE"
CPA_ENDPOINT=https://your-cpa-endpoint
CPA_TOKEN=your-token
CPA_INTERVAL=1800
CPA_QUOTA_THRESHOLD=100
CPA_EXPIRY_THRESHOLD_DAYS=3
CPA_ENABLE_REFRESH=true
CPA_WORKER_THREADS=8
EOF
fi

# 创建软链接，让根目录的 src/settings.py 能正确读取到它
ln -sf "$ENV_FILE" "$APP_ENV_FILE"

# 执行传递进来的命令（通常是 python -m web.server）
exec "$@"
