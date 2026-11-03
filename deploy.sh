#!/usr/bin/env bash
set -e

echo "============================================="
echo "   V2Ray Log Analyzer - Docker 一键部署脚本"
echo "============================================="

# 检查 Docker 是否安装
if ! command -v docker &> /dev/null; then
    echo "未检测到 Docker，正在尝试自动安装 Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
fi

# 检查 Docker Compose
if ! docker compose version &> /dev/null; then
    echo "提示: 当前系统推荐使用 docker compose (v2)。"
fi

# 配置 V2Ray 日志目录
DEFAULT_V2RAY_LOG="/var/log/v2ray"
if [ ! -d "$DEFAULT_V2RAY_LOG" ]; then
    DEFAULT_V2RAY_LOG="$(pwd)"
fi

echo ""
read -p "请输入 VPS 上的 V2Ray 日志绝对路径 [默认: $DEFAULT_V2RAY_LOG]: " USER_LOG_PATH
LOG_PATH="${USER_LOG_PATH:-$DEFAULT_V2RAY_LOG}"

if [ ! -d "$LOG_PATH" ]; then
    echo "警告: 目录 $LOG_PATH 当前不存在，已为您自动创建该目录。"
    mkdir -p "$LOG_PATH"
fi

# 配置管理员密码
DEFAULT_PASS="admin888"
echo ""
read -p "请设置 Web 访问登录密码 [默认: $DEFAULT_PASS]: " USER_PASS
ADMIN_PASSWORD="${USER_PASS:-$DEFAULT_PASS}"

# 配置端口
DEFAULT_PORT="8848"
read -p "请设置 Web 访问端口 [默认: $DEFAULT_PORT]: " USER_PORT
WEB_PORT="${USER_PORT:-$DEFAULT_PORT}"

# 创建持久化存储目录
mkdir -p data

# 生成 .env 文件供 docker-compose 读取
cat <<EOF > .env
PORT=$WEB_PORT
LOG_DIR=/logs
ACCESS_LOG_PATH=/logs/access.log
DB_PATH=/data/v2ray_stats.db
ADMIN_USER=admin
ADMIN_PASSWORD=$ADMIN_PASSWORD
HOST_LOG_DIR=$LOG_PATH
EOF

# 更新 docker-compose.yml 挂载路径
cat <<EOF > docker-compose.yml
version: "3.8"

services:
  v2ray-analyzer:
    build: .
    image: v2ray-analyzer:latest
    container_name: v2ray-analyzer
    restart: unless-stopped
    ports:
      - "${WEB_PORT}:8848"
    volumes:
      - ${LOG_PATH}:/logs:ro
      - ./data:/data
    environment:
      - PORT=8848
      - LOG_DIR=/logs
      - ACCESS_LOG_PATH=/logs/access.log
      - DB_PATH=/data/v2ray_stats.db
      - ADMIN_USER=admin
      - ADMIN_PASSWORD=${ADMIN_PASSWORD}
EOF

echo ""
echo "正在构建并启动 Docker 容器..."
docker compose down 2>/dev/null || true
docker compose up -d --build

# 获取本机公网 IP
SERVER_IP=$(curl -s4 ifconfig.me || curl -s4 ip.sb || echo "你的VPS公网IP")

echo ""
echo "============================================="
echo "🎉 V2Ray 日志分析服务已成功启动！"
echo "---------------------------------------------"
echo "🌐 访问地址: http://${SERVER_IP}:${WEB_PORT}"
echo "👤 用户名:   admin"
echo "🔑 密码:     ${ADMIN_PASSWORD}"
echo "📁 监听日志: ${LOG_PATH}"
echo "💾 数据持久化: $(pwd)/data"
echo "============================================="
echo "日常管理命令："
echo "  查看运行日志: docker compose logs -f"
echo "  停止服务:     docker compose down"
echo "  重启服务:     docker compose restart"
echo "============================================="
