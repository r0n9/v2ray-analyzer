#!/usr/bin/env bash
set -e

# 权限检测：如果非 root 且无 docker 权限时才尝试 sudo 提权
if [ "$EUID" -ne 0 ]; then
    if ! docker info &>/dev/null; then
        echo "提示: 检测到当前非 root 用户且无 Docker 运行权限，正在尝试通过 sudo 提升权限..."
        if command -v sudo &> /dev/null; then
            exec sudo bash "$0" "$@"
        else
            echo "错误: 系统未安装 sudo 且当前用户无 Docker 运行权限。请将用户加入 docker 用户组或切换至 root 账户后再执行！"
            exit 1
        fi
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

# 读取已有配置作为默认值
SAVED_LOG_DIR=""
SAVED_PORT=""
SAVED_PASS=""
if [ -f .env ]; then
    SAVED_LOG_DIR=$(grep -E '^HOST_LOG_DIR=' .env | cut -d= -f2- || true)
    SAVED_PORT=$(grep -E '^PORT=' .env | cut -d= -f2- || true)
    SAVED_PASS=$(grep -E '^ADMIN_PASSWORD=' .env | cut -d= -f2- || true)
fi

# 3. 配置 V2Ray 日志目录（默认 /var/log/v2ray/）
DEFAULT_V2RAY_LOG="${SAVED_LOG_DIR:-/var/log/v2ray}"
echo ""
echo "正在配置 V2Ray 日志路径..."
read -p "请输入 VPS 上的 V2Ray 日志绝对路径 [默认: ${DEFAULT_V2RAY_LOG}]: " USER_LOG_PATH
LOG_PATH="${USER_LOG_PATH:-$DEFAULT_V2RAY_LOG}"
LOG_PATH="${LOG_PATH%/}"

# 检查日志目录并设置适当的只读权限
if [ ! -d "$LOG_PATH" ]; then
    echo "提示: 目录 $LOG_PATH 当前不存在，已为您自动创建该目录..."
    mkdir -p "$LOG_PATH"
fi
echo "正在检查并配置目录读取权限..."
chmod -R a+rX "$LOG_PATH" 2>/dev/null || true

# 4. 配置管理员密码
DEFAULT_PASS="${SAVED_PASS:-admin888}"
echo ""
read -p "请设置 Web 访问登录密码 [默认: $DEFAULT_PASS]: " USER_PASS
ADMIN_PASSWORD="${USER_PASS:-$DEFAULT_PASS}"

# 5. 配置端口
DEFAULT_PORT="${SAVED_PORT:-8848}"
read -p "请设置 Web 访问端口 [默认: $DEFAULT_PORT]: " USER_PORT
WEB_PORT="${USER_PORT:-$DEFAULT_PORT}"

# 6. 持久化数据目录检查与异常修复
mkdir -p data
if [ -f "data/v2ray_stats.db" ] && [ ! -s "data/v2ray_stats.db" ]; then
    echo "提示: 检测到此前启动失败残留的 0 字节数据库文件，已自动清理..."
    rm -f data/v2ray_stats.db*
fi
chmod -R 777 data 2>/dev/null || true

# 7. 生成 / 更新 .env 与 docker-compose.yml 文件
cat <<EOF > .env
PORT=$WEB_PORT
LOG_DIR=/logs
ACCESS_LOG_PATH=/logs/access.log
DB_PATH=/data/v2ray_stats.db
ADMIN_USER=admin
ADMIN_PASSWORD=$ADMIN_PASSWORD
HOST_LOG_DIR=$LOG_PATH
EOF

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
    security_opt:
      - seccomp:unconfined
    volumes:
      # V2Ray 日志目录（只读挂载）
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

# 8. 核心优化：自动检查、停止并删除旧容器，清理旧镜像
CONTAINER_NAME="v2ray-analyzer"
IMAGE_NAME="v2ray-analyzer:latest"

echo ""
echo "============================================="
echo "🧹 正在清理旧容器与旧镜像..."
echo "============================================="

# 停止并移除 docker compose 编排的容器
echo "-> 停止旧服务编排..."
docker compose down --remove-orphans 2>/dev/null || true

# 强制停止并删除任何同名容器（包含独立创建或异常退出的容器）
if docker ps -a --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}$"; then
    echo "-> 检测到已有同名容器 [${CONTAINER_NAME}]，正在强制停止并删除..."
    docker stop "${CONTAINER_NAME}" 2>/dev/null || true
    docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
fi

# 删除旧镜像
if docker images --format '{{.Repository}}:{{.Tag}}' | grep -Eq "^${IMAGE_NAME}$"; then
    echo "-> 检测到已有镜像 [${IMAGE_NAME}]，正在清理旧镜像..."
    docker rmi -f "${IMAGE_NAME}" 2>/dev/null || true
fi

# 清理虚悬缓存镜像（dangling images）
DANGLING=$(docker images -f "dangling=true" -q)
if [ -n "$DANGLING" ]; then
    echo "-> 正在清理无用构建虚悬镜像..."
    docker rmi -f $DANGLING 2>/dev/null || true
fi

# 9. 无缓存重新构建并部署新容器
echo ""
echo "============================================="
echo "🔨 正在重新构建并启动新镜像容器..."
echo "============================================="
docker compose build --no-cache
docker compose up -d

# 10. 容器健康状态检查
echo "-> 验证服务启动状态..."
sleep 2

if docker ps --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}$"; then
    echo "-> ✅ 容器已成功进入运行状态！"
else
    echo "-> ❌ 警告：容器启动后未正常运行，以下为容器最近日志输出："
    docker compose logs --tail=30
    exit 1
fi

# 获取本机公网 IP
SERVER_IP=$(curl -s4 ifconfig.me || curl -s4 ip.sb || echo "你的VPS公网IP")

echo ""
echo "============================================="
echo "🎉 V2Ray 日志分析服务已成功全新部署启动！"
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
