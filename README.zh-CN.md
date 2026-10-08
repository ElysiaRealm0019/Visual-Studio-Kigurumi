# KigCraft

中文 | [English](README.md)

<p align="center">
  <img src="frontend/public/logo.png" alt="KigCraft logo" width="160" />
</p>

KigCraft 是一个用于制作 Kigurumi 头壳预览图的网页工具。每个角色是一个项目：在右侧和 Agent 对话，让它分析参考图、生成和修改正视图、出四视图；也可以在编辑器里用手动工具（比例、脸型、眼睛、眉毛、嘴巴、液化、标注、局部生成）直接调整。所有结果都进入项目的版本历史。

开发者：SeaRabbit / 海兔  
用户交流群：QQ 934715528

## 功能

- 项目工作区：类似 VS Code 的布局，左侧是资源管理器（参考图、版本树）和工具面板，中间是带标签页的画布，右侧是 Agent 对话，底部是状态栏；支持深色和浅色主题。
- 手动编辑：比例、脸型、眼睛、眉毛、嘴巴的参数会实时变形图片（浏览器内的网格变形，不调用 AI）；还有液化、标注和局部生成。保存后成为新版本（Ctrl+S）。
- 对话式流程：由一个 LLM 充当“中间件”，根据对话自动调用参考图分析、正视图生成、正视图修改和四视图生成；它也知道你手动保存的版本。
- 关键节点暂停：正视图生成后必须由用户确认（点击“就用这张”或在对话里明确同意），才会生成四视图，避免浪费生成额度。
- 每轮对话有生成次数上限（`AGENT_MAX_GENERATIONS_PER_TURN`），防止模型反复生成。
- 历史对话保存在本地，可随时回到之前的设计继续修改。
- LLM、图片和对话编排后端都可以单独配置（Codex、Claude Code、SiliconFlow、火山方舟等）。
- 支持中文、英文和日文界面。

## 许可证

KigCraft 使用 GPL-3.0-or-later 发布。详见 [LICENSE](LICENSE)。

## 环境要求

- Docker Desktop 或带 Compose 的 Docker Engine
- 只开发前端时需要 Node.js 22 或更新版本
- 只开发后端时需要 Python 3.12 或更新版本
- 使用 `GENERATION_PROVIDER=codex` 时需要已登录的 Codex CLI 配置

## 快速启动

```powershell
Copy-Item .env.example .env
# 对外提供服务前，请先修改 .env 里的所有 change-me-* 值。
docker compose up --build
```

使用 Codex 生成前，请在仓库根目录自行创建 `ref/` 并放入成品参考图。详见[成品参考图](#成品参考图)。

### 不用 Docker 本地启动

本地开发可以不用 Docker：生成任务在 API 进程里执行，状态存在 SQLite，结果存在 `runtime/`，用不到 Postgres、Redis 和 MinIO。

```powershell
Copy-Item .env.example .env   # 设置 LLM_PROVIDER / IMAGE_PROVIDER 等
.\scripts\start-local.ps1
```

脚本会在缺少依赖时自动创建 `backend\.venv` 并执行 `npm install`，再启动后端（`127.0.0.1:18000`）和前端（<http://localhost:5173>），Ctrl+C 同时停止两者。加 `-Lan` 可以让同一局域网的手机访问前端。如果 `codex` 不在 PATH 中，脚本会自动使用官方安装位置的 `codex.exe`。

Docker 启动时的本地地址：

- 前端：<http://localhost:15173>
- API 健康检查：<http://localhost:18000/health>
- MinIO 控制台：<http://localhost:19001>

## 生成 provider

`GENERATION_PROVIDER=codex` 会在后端容器里调用 Codex CLI。运行时需要挂载已经登录的 Codex 配置目录：

```powershell
Copy-Item -Recurse "$env:USERPROFILE\.codex" ".\runtime\codex-home"
docker compose up --build
```

Linux 服务器上可以把已登录的 Codex 配置目录复制到主机，然后设置：

```dotenv
GENERATION_PROVIDER=codex
CODEX_PATH=codex
CODEX_CONFIG_DIR=/home/deploy/.codex
CODEX_PRODUCT_REFERENCE_PATH=ref/product-reference.png
```

### 分开配置 LLM 和图片后端

生成流程用到两类模型：

- **LLM**（`LLM_PROVIDER`）：参考图安全检查和细节分析。可选 `codex`、`claude_code`。
- **图片**（`IMAGE_PROVIDER`）：正视图、四视图和局部重绘。可选 `codex`、`codex_bridge`、`siliconflow`、`ark`。

两项留空时由 `GENERATION_PROVIDER` 推导，旧配置无需修改。完全不依赖 Codex 的组合示例：

```dotenv
LLM_PROVIDER=claude_code
IMAGE_PROVIDER=siliconflow
SILICONFLOW_API_KEY=你的密钥
```

**Claude Code**：需要挂载已登录的 Claude Code 配置目录（容器内路径为 `/root/.claude`）：

```powershell
Copy-Item -Recurse "$env:USERPROFILE\.claude" ".\runtime\claude-home"
```

Claude 模型不能生成图片，因此 `LLM_PROVIDER=claude_code` 时必须另外配置一个图片后端。macOS 的登录凭据保存在钥匙串里，复制目录不会带上凭据，需要在 Linux 或 Windows 主机上登录后再复制。

**SiliconFlow**：默认使用 `Qwen/Qwen-Image-Edit-2509`，单次请求最多传 3 张参考图：

- 正视图：角色正面参考 + `ref/product-reference.png` + 其余参考拼成的一张图；
- 四视图：已确认的正视图 + `ref/turnaround-reference.png`；
- 局部重绘：只把蒙版区域裁出来编辑，再按蒙版合成回原图。

四视图按模型原生分辨率输出，不做放大。请求时会关闭平台水印，KigCraft 自己的 AI 生成水印会强制添加。

**火山方舟（Agent Plan）**：`IMAGE_PROVIDER=ark`，默认模型 `doubao-seedream-5-0-pro`，所有参考图放在同一个 `image` 列表里一起发送，数量上限由 `ARK_MAX_REFERENCE_IMAGES` 控制（默认 4 张，超出部分会拼成一张拼图）。需要 Agent Plan 的专属 API Key（`ARK_API_KEY`），其他方舟 Key 不能用于 Agent Plan。计费按 AFP 抵扣：第一张输入图免费，之后每张 10 AFP；每张输出图 150 AFP（超过约 261 万像素为 300）。四视图默认请求 `1920x1280`（3:2，按 150 AFP 计）；如果设成 `2K`，模型会按参考图比例自选尺寸，后端再用背景色补边到 3:2。

可以先用探测脚本确认效果：

```powershell
$env:SILICONFLOW_API_KEY = "你的密钥"
backend\.venv\Scripts\python tools\siliconflow_probe.py size-test
```

### 对话助手（编排 LLM）

对话界面由一个编排 LLM 驱动，负责理解用户意图并调用工具。它与上面的 `LLM_PROVIDER`（参考图安全检查和细节分析）是两回事：

- `AGENT_LLM_PROVIDER=openai_compatible`（默认）：任意支持 tool calling 的 OpenAI 兼容接口。默认指向火山方舟 Agent Plan 的 `doubao-seed-2-0-pro`，并关闭深度思考，单步约 5 秒。`AGENT_LLM_API_KEY` 留空时会复用 `ARK_API_KEY`。
- `AGENT_LLM_PROVIDER=claude_code`：用已登录的 Claude Code 做编排，不需要额外的 Key，但每一步都要启动一次 CLI，响应较慢。

推荐组合：

```dotenv
LLM_PROVIDER=claude_code
IMAGE_PROVIDER=ark
ARK_API_KEY=你的 Agent Plan 专属 Key
```

### 成品参考图

仓库里不包含成品参考图。使用 Codex 生成前，需要自己在仓库根目录创建 `ref/` 目录，并放入以下文件：

| 文件 | 用途 |
| --- | --- |
| `ref/product-reference.png` | 正视图生成用的成品风格参考 |
| `ref/turnaround-reference.png` | 四视图生成用的成品风格参考 |

请使用 PNG 格式，并保持文件名一致。

这些图片用于约束生成结果的成品头壳风格，例如白底棚拍、材质、假发质感、构图和打光。它们与用户在界面上传的角色参考图不是同一类文件。

`ref/` 已加入 `.gitignore`，参考图会保留在本地，不会被提交到 Git。

```powershell
New-Item -ItemType Directory -Force ref
# 把你的参考图复制到 ref/ 目录
```

如需自定义正视图参考图路径，可在 `.env` 中修改：

```dotenv
CODEX_PRODUCT_REFERENCE_PATH=ref/product-reference.png
```

`GENERATION_PROVIDER=codex_bridge` 用于让 Codex CLI 在后端容器外运行。启动方式：

```powershell
.\tools\start_codex_bridge.ps1
```

非本地开发环境请设置自己的 `CODEX_BRIDGE_TOKEN`。

Fixture 和 mock 生成只用于测试或本地 smoke run，不要在生产环境启用。

## 部署

部署前先在服务器上准备生产 `.env`。至少需要设置：

- `APP_ENV=production`
- 强密码形式的 `POSTGRES_PASSWORD`、`MINIO_ROOT_PASSWORD`、`JWT_SECRET` 和 `ADMIN_AUDIT_PASSWORD`
- 生产环境的 `CORS_ALLOWED_ORIGINS`
- `GENERATION_PROVIDER=codex` 或 `codex_bridge`
- `ALLOW_FIXTURE_GENERATION=false`

通过 SSH 部署当前 Git 提交：

```powershell
.\scripts\deploy-ssh.ps1 `
  -KeyPath "$env:USERPROFILE\.ssh\id_ed25519" `
  -SshTarget "deploy@example.com" `
  -RemoteAppDir "/opt/kigcraft"
```

脚本会上传当前提交的 `git archive`，在服务器上解压，检查生产环境没有使用 fixture 生成，然后用 Docker Compose 重建 `api`、`worker` 和 `frontend`。

## 开发

前端：

```powershell
cd frontend
npm install
npm run dev
npm run build
```

后端：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
.\.venv\Scripts\python -m pytest
```
