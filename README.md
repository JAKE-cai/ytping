# YTPing-网络质量监控

基于 ICMP Ping 的业务地址连通性监控平台，支持高频探测、历史延迟折线图和七天前数据压缩统计。浏览器访问后可在导航栏打开**使用说明**（`/help.html`）。

## 功能特性

| 特性 | 说明 |
|------|------|
| 高频探测 | 每个目标独立异步任务，最低 500 ms / 次（默认 1 s/次） |
| 实时看板 | 卡片展示当前延迟、丢包率，每 10 s 自动刷新 |
| 延迟折线图 | 支持 1H / 6H / 24H / 3D / 7D 快选，或自定义时间范围 |
| Min / Avg / Max | 折线图同时展示三条线 + 丢包率副轴 |
| 丢包记录 | 可翻页浏览历史丢包时间点 |
| 七天前压缩 | 超过 7 天的原始数据自动压缩为每小时桶（1-30 ms … 丢包 共 8 档） |
| 历史统计 | 饼图 + 表格展示压缩后的延迟分布 |
| Docker 部署 | 单容器，SQLite 持久化，开箱即用 |

## 快速启动

```bash
# 构建并启动
docker compose up -d --build

# 查看日志
docker compose logs -f
```

浏览器访问 **http://localhost:3000**（默认账号 `admin`，首次密码见部署说明）

## 本地开发

```bash
# 安装依赖
cd backend
pip install -r requirements.txt

# 启动（数据库路径可覆盖）
DB_PATH=./dev.db uvicorn app.main:app --reload --port 8000
```

> **注意**：本地运行需要系统安装 `ping`（Linux/macOS 均自带；Windows 需使用 WSL 或在容器内运行）。

## 目录结构

```
ytping/
├── Dockerfile
├── docker-compose.yml
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py          # FastAPI 入口
│       ├── database.py      # SQLite 初始化 & WAL 配置
│       ├── pinger.py        # 异步 Ping 管理器 + 批量写入
│       ├── compressor.py    # 每小时数据压缩任务
│       ├── state.py         # 共享单例
│       └── routers/
│           ├── targets.py   # 目标 CRUD
│           └── metrics.py   # 状态 / 图表 / 历史查询
└── frontend/
    └── index.html           # Vue 3 + ECharts SPA（无构建步骤）
```

## 性能说明

- **批量写入**：Ping 结果先进内存队列，每秒批量 INSERT，减少 SQLite 写事务频率。
- **WAL 模式**：读写并发，避免查询阻塞写入。
- **自动降采样**：图表查询按时间范围自动选择桶大小（最多约 720 个数据点）。
- **批量状态接口** `/api/metrics/all-status`：一次 SQL 聚合全部目标，避免 N 次单独请求。
- **数据压缩**：原始数据保留 7 天，之后压缩为每小时桶，显著降低存储增长。
