#!/usr/bin/env bash
set -e

# 自动检测与请求 root 权限（/var/log 属系统保护目录）
if [ "$EUID" -ne 0 ]; then
    echo "提示: 检测到当前非 root 用户。由于访问并挂载 /var/log/v2ray/ 需要特权，正在尝试通过 sudo 提升权限..."
    if command -v sudo &> /dev/null; then
        exec sudo bash "$0" "$@"
    else
        echo "错误: 系统未安装 sudo 且当前非 root 运行。请切换至 root 账户后再执行此脚本！"
        exit 1
    fi
fi

echo "============================================="
echo "   V2Ray Log Analyzer - Docker 一键部署脚本"
echo "============================================="

# 1. 检查 Docker 是否安装
if ! command -v docker &> /dev/null; then
    echo "未检测到 Docker，正在尝试自动安装 Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
fi

# 2. 检查 Docker Compose
if ! docker compose version &> /dev/null; then
    echo "提示: 当前系统推荐使用 docker compose (v2)。"
fi

# 3. 配置 V2Ray 日志目录（默认 /var/log/v2ray/）
DEFAULT_V2RAY_LOG="/var/log/v2ray"

echo ""
echo "正在配置 V2Ray 日志路径..."
read -p "请输入 VPS 上的 V2Ray 日志绝对路径 [默认: /var/log/v2ray/]: " USER_LOG_PATH
LOG_PATH="${USER_LOG_PATH:-$DEFAULT_V2RAY_LOG}"
# 去除末尾斜杠保持标准格式
LOG_PATH="${LOG_PATH%/}"

# 检查日志目录并设置适当的只读权限
if [ ! -d "$LOG_PATH" ]; then
    echo "提示: 目录 $LOG_PATH 当前不存在，已为您自动创建该目录..."
    mkdir -p "$LOG_PATH"
fi

# 确保日志目录与日志文件对容器具备只读权限（避免 root 600 权限导致无权读取）
echo "正在检查并配置目录读取权限..."
chmod -R a+rX "$LOG_PATH" 2>/dev/null || true

# 4. 配置管理员密码
DEFAULT_PASS="admin888"
echo ""
read -p "请设置 Web 访问登录密码 [默认: $DEFAULT_PASS]: " USER_PASS
ADMIN_PASSWORD="${USER_PASS:-$DEFAULT_PASS}"

# 5. 配置端口
DEFAULT_PORT="8848"
read -p "请设置 Web 访问端口 [默认: $DEFAULT_PORT]: " USER_PORT
WEB_PORT="${USER_PORT:-$DEFAULT_PORT}"

# 创建持久化存储目录并赋予完全读写权限，避免 SQLite 容器挂载 I/O 报错
mkdir -p data
chmod -R 777 data 2>/dev/null || true

# 生成 .env 文件供 docker compose 读取
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
echo "📁 监听日志: ${LOG_PATH}/"
echo "💾 数据持久化: $(pwd)/data"
echo "============================================="
echo "日常管理命令："
echo "  查看运行日志: docker compose logs -f"
echo "  停止服务:     docker compose down"
echo "  重启服务:     docker compose restart"
echo "============================================="
