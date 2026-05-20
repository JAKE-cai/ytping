# 镜像 `ytping:1.1` 离线包与运行说明

当前推荐 **`ytping:1.1`**（含登录页密码框被轮询清空等前端修复）。旧包 **`ytping:1.0`** 仍可 `docker load`，但建议升级到 1.1。

## 离线导入

压缩包为 **Docker `save` 的 tar 再 gzip**，导入前需解压为 tar，或管道解压：

```bash
# 方式一：先解压再 load
gzip -d ytping_1.1.tar.gz
docker load -i ytping_1.1.tar

# 方式二：管道（Linux / macOS / Git Bash）
gunzip -c ytping_1.1.tar.gz | docker load
```

导入成功后本地会有镜像 **`ytping:1.1`**。

---

## 端口映射 `-p`

应用监听容器内 **3000**（HTTP + 静态前端 + API）。

| 场景 | 示例 |
|------|------|
| 宿主机同样用 3000 | `-p 3000:3000` |
| 仅本机可访问 | `-p 127.0.0.1:3000:3000` |
| 换宿主机端口 | `-p 8080:3000` |

---

## 环境变量 `-e`

| 变量 | 说明 | 示例 |
|------|------|------|
| `DB_PATH` | SQLite 文件路径（默认 `/data/monitor.db`） | `-e DB_PATH=/data/monitor.db` |
| `ENV` | `production` 时关闭 Swagger 文档（默认即为 production） | `-e ENV=production` |
| `ALLOWED_ORIGINS` | CORS 允许来源，逗号分隔；留空则等同 `*` | `-e ALLOWED_ORIGINS=https://monitor.example.com` |
| `PYTHONUNBUFFERED` | 建议 `1`，日志实时输出 | `-e PYTHONUNBUFFERED=1` |

---

## 数据持久化 `-v`

数据库与 WAL 等文件写在 **`/data`**（容器内）。请把宿主机目录挂载到 **`/data`**，避免删容器丢数据。

```bash
# 示例：数据落在当前目录下的 ytping-data 文件夹
mkdir -p ./ytping-data
docker run -d --name ytping \
  --restart unless-stopped \
  --cap-add=NET_RAW \
  -p 3000:3000 \
  -v "$(pwd)/ytping-data:/data" \
  -e PYTHONUNBUFFERED=1 \
  ytping:1.1
```

Windows PowerShell 示例：

```powershell
New-Item -ItemType Directory -Force -Path .\ytping-data | Out-Null
docker run -d --name ytping `
  --restart unless-stopped `
  --cap-add=NET_RAW `
  -p 3000:3000 `
  -v "${PWD}\ytping-data:/data" `
  -e PYTHONUNBUFFERED=1 `
  ytping:1.1
```

浏览器访问：`http://localhost:3000`（首次请尽快修改默认管理员密码）。

---

## 能力说明

ICMP ping 需要 **`--cap-add=NET_RAW`**（与仓库 `docker-compose.yml` 一致）。若省略，探测可能失败。
