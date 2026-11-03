# V2Ray Log Analyzer (V2Ray 访问日志分析与可视化大盘)

专为运行在 VPS 上的 V2Ray 打造的轻量级、自动化访问日志分析与可视化监控服务。

采用 **Docker 容器化一键部署**，零依赖、极低资源占用（内存 $< 50\text{MB}$），提供多维度流量统计、24小时峰谷趋势、域名 Top 排行榜、以及**深度客户端 IP 画像与行为穿透分析**。

---

## 🌟 核心特性

- 🚀 **极轻量零依赖**：基于 Alpine Linux 与 FastAPI 构建，内存占用极低，无需 VPS 预先安装 Python 或数据库。
- 📦 **Docker 一键启动**：提供 `deploy.sh` 交互式部署脚本与 `docker-compose.yml`，几秒内即可运行。
- 👥 **客户端 IP 专项画像 (核心需求)**：
  - 自动识别客户端 IP 物理位置（省市、运营商识别）；
  - 支持**自定义设备别名备注**（如将 `116.237.x.x` 标注为“我的 iPhone”或“家里软路由”）；
  - **IP 深度下钻**：点击任意 IP 查看其专属 24 小时活跃时段热力图、访问域名 Top 15、端口分布及最近 30 条原始访问流水。
- 📊 **全自动增量采集与历史兼容**：
  - 无需再定时运行 `split_logs.sh` 脚本；
  - 自动游标跟踪 `access.log`，平滑增量写入本地 SQLite（WAL 高性能模式）；
  - 首次启动自动扫描并无缝导入现存的所有 `YYYY_MM_DD.log` 历史归档日志（去重写入）。
- 🔒 **安全访问控制**：内置 HTTP Basic 认证，防止公网未授权访问。
- 🔍 **全功能日志明细检索器**：支持按日期、客户端 IP、目标域名、端口、协议进行多条件组合过滤和分页查看。

---

## 🛠️ 项目目录结构

```text
v2ray_log/
├── app/
│   ├── api.py            # FastAPI RESTful 接口 (大盘、IP画像、检索、别名)
│   ├── collector.py      # 增量日志采集器与历史文件自动导入
│   ├── database.py       # SQLite WAL 模式数据库操作与聚合引擎
│   ├── geo.py            # IP 地理位置解析与设备别名存储
│   ├── main.py           # 服务主入口与后台扫描调度线程
│   └── parser.py         # V2Ray 日志分词解析器 (兼容标准与切分格式)
├── static/
│   ├── index.html        # 响应式 Web 仪表盘单页 (Tailwind CSS + Vue 3)
│   └── app.js            # 前端逻辑与 ECharts 交互可视化图表
├── config.json           # 本地默认配置文件
├── Dockerfile            # Alpine 轻量容器镜像构建文件
├── docker-compose.yml    # Docker Compose 服务定义文件
├── deploy.sh             # VPS 一键交互式部署脚本
└── requirements.txt      # Python 依赖清单
```

---

## 🚀 VPS Docker 部署步骤 (推荐)

### 第一步：将项目文件上传至 VPS
将本目录下的代码包上传到 VPS（例如 `/opt/v2ray-analyzer` 目录）：
```bash
# 在本地机器执行（或通过 git clone / scp 上传）：
scp -r /Users/rong/Documents/PVE/v2ray_log user@<your-vps-ip>:/opt/v2ray-analyzer
```

### 第二步：运行一键部署脚本
登录 VPS 终端并执行：
```bash
cd /opt/v2ray-analyzer
chmod +x deploy.sh
./deploy.sh
```

脚本会交互式引导您确认：
1. **V2Ray 日志路径**：默认是 `/var/log/v2ray`（脚本会自动将该目录挂载为只读）；
2. **Web 登录密码**：默认 `admin888`（可自定义）；
3. **Web 访问端口**：默认 `8848`。

> 💡 **已有历史日志导入说明**：
> 如果您之前通过 `split_logs.sh` 生成的历史 `2026_xx_xx.log` 日志文件，只需放置在 V2Ray 日志目录下，容器启动后会自动扫描并一次性导入数据库！

---

## 🔧 Docker Compose 手动管理

如果您熟悉 Docker Compose，也可以直接执行：

```bash
# 启动并在后台运行
docker compose up -d --build

# 查看实时运行日志
docker compose logs -f

# 停止服务
docker compose down

# 重启服务
docker compose restart
```

---

## 🌐 访问与使用

1. 在浏览器打开：`http://<VPS_IP>:8848`
2. 输入认证信息：
   - 用户名：`admin`
   - 密码：部署时设置的密码（默认 `admin888` 或 `changeme888`）
3. **核心功能使用指南**：
   - **大盘看板**：右上角切换历史日期，查看当日 24 小时流量曲线与热门目标域名。
   - **IP 画像分析**：切换到「客户端 IP 分析」标签，查看所有连入设备的 IP 归属地，点击 ✏️ 可以为 IP 备注名字（例如“家里主宽带”），点击「画像穿透」进入专属深度报告。
   - **日志检索**：随时按 IP 或域名进行日志搜索与溯源。
   - **立即同步**：点击顶部的「立即同步」按钮可即时刷新最新数据。

---

## 🔒 进阶配置：Nginx 反向代理与 HTTPS (可选)

为了在公网环境下更安全地访问，推荐通过 Nginx 为该面板配置域名与 SSL 证书：

```nginx
server {
    listen 443 ssl http2;
    server_name v2ray-stats.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8848;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

---

## 💻 本地直接运行（Mac / Linux 开发测试）

如果想在当前电脑直接预览查看已有的 13 万条历史日志：
```bash
# 1. 激活虚拟环境
source .venv/bin/activate

# 2. 启动服务
python3 -m app.main
```
随后直接在 Mac 浏览器打开：`http://localhost:8848`，账号 `admin`，密码 `changeme888`。
