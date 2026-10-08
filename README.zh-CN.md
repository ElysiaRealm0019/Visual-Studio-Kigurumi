# Visual Studio Kigurumi (V.S.K)

中文 | [English](README.md)

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark-mode.png" />
    <img src="docs/logo.png" alt="Visual Studio Kigurumi" width="160" />
  </picture>
</p>

V.S.K 是一个设计 Kigurumi 头壳的网页工具。上传角色参考图，和设计助手对话，先得到 2D 角色设计稿，再得到头壳成品正视图和四视图。每张图都能在内置编辑器里手动调整，所有结果都保存在项目的版本历史里。

V.S.K 是基于 [KigCraft](https://kigcraft.com)（原作者 SeaRabbit / 海兔，用户交流群 QQ 934715528）开发的独立项目。对话式工作流、工作区和生成管线来自 KigCraft；V.S.K 在此基础上加入了可切换的模型后端、设置页、编辑器改进和提示词调优。

## 工作流程

每个角色是一个项目。右侧的设计助手分两个阶段推进，每一步花钱的生成之前都会停下来等你确认：

1. **角色设计**：分析参考图，生成 2D 设计稿（默认正视图，也可以要四视图）。如果缺少关键信息或参考图互相矛盾，比如不确定是哪个角色、耳朵被帽子挡住，会先问你一个简短的问题。
2. **头壳**：你认可设计稿后，生成头壳正视图（成品照片风格）；再认可后，生成头壳四视图。

任何一步都可以在对话里提修改，也可以自己在编辑器里改完保存为新版本，助手也看得到这些版本。

## 功能

- **IDE 风格工作区**：资源管理器（参考图、版本树）、带标签页的画布、助手面板和状态栏，支持深色和浅色主题。
- **手动编辑器**：在浏览器里实时做网格变形，不调用 AI。
  - 比例、脸型、眼睛（大小、眼睑、虹膜、眼尾）、眉毛、嘴巴滑块，关键点可以拖动；
  - 液化、标注；
  - 对涂抹区域做局部重绘。
  - 保存即生成新版本（Ctrl+S）。
- **本地草稿**：没保存的编辑按版本存在浏览器里（IndexedDB），切回来或刷新页面后自动恢复。
- **设置页**（`/settings`）：
  - 切换对话助手 LLM、参考图分析 LLM 和生图后端；
  - 直接填写 API Key、接口地址和模型名，不用改 `.env`。
- **防止浪费额度**：阶段之间必须确认，每条消息另有生成次数上限（`AGENT_MAX_GENERATIONS_PER_TURN`）。
- **多语言**：中文、英文、日文界面。

## 快速开始（Windows，不用 Docker）

本地开发不需要 Docker：任务在 API 进程里运行，状态存在 SQLite，输出写到 `runtime/`。

```powershell
Copy-Item .env.example .env
.\scripts\start-local.ps1
```

这个脚本会：

- 第一次运行时创建 `backend\.venv` 并安装前端依赖；
- 启动 API（`127.0.0.1:18000`）和前端（<http://localhost:5173>）；
- 加 `-Lan` 参数后，局域网里的其他设备也能访问；
- 按 Ctrl+C 同时停止两者。

启动后点右上角齿轮进入**设置**，选好后端并填写 API Key。

## 后端

一共三个角色，可以在设置页或 `.env` 里分别配置：

| 角色 | 配置项 | 可选 |
| --- | --- | --- |
| 对话助手（决定调用哪些工具） | `AGENT_LLM_PROVIDER` | `openai_compatible`（任意支持工具调用的 OpenAI 兼容 API，默认火山方舟 `doubao-seed-2-0-pro`）、`claude_code` |
| 参考图分析（安全检查 + 细节分析） | `LLM_PROVIDER` | `openai_compatible`（需要支持图片输入的模型）、`codex`、`claude_code` |
| 生图 | `IMAGE_PROVIDER` | `ark`（Seedream）、`siliconflow`（Qwen-Image-Edit）、`codex`、`codex_bridge` |

只用一个火山方舟 Agent Plan Key 的配置：

```dotenv
AGENT_LLM_PROVIDER=openai_compatible
LLM_PROVIDER=openai_compatible
IMAGE_PROVIDER=ark
ARK_API_KEY=你的-agent-plan-key
```

对话助手和参考图分析的 Key 留空时，如果接口地址是火山方舟的，会自动沿用方舟 Key。

### 设置保存在哪里

- 设置页保存的值写在 `runtime/settings-overrides.json`，优先于 `.env`。点"全部恢复为 .env"会清掉它们。
- API Key 只写不读：页面上只显示是否已配置和末 4 位。
- Key 以明文保存在本机，`runtime/` 已加入 gitignore。
- `APP_ENV=production` 时设置页只读。

### 各后端说明

- **火山方舟**：需要 Agent Plan 专属 Key，其他方舟 Key 不能用。
  - 每次最多发送 `ARK_MAX_REFERENCE_IMAGES` 张参考图（默认 4 张），多出来的会拼成一张联系表。
  - 四视图默认请求 `1920x1280`。
- **SiliconFlow**：每次最多发送 3 张图。
  - 局部重绘只编辑蒙版区域的裁切图，再按蒙版合成回去。
  - 可以用 `tools\siliconflow_probe.py` 先测试接口。
- **Codex / Claude Code**：调用本机已登录的命令行工具，不需要 API Key，但每一步都更慢。
  - `codex_bridge` 在后端容器外运行 Codex：用 `tools\start_codex_bridge.ps1` 启动，并设置 `CODEX_BRIDGE_TOKEN`。
  - Claude 不能生图，只能用于两个 LLM 角色。
- 所有生成图都会加上 V.S.K 自己的"AI 生成"水印，平台水印已关闭。

### 成品参考图（可选）

仓库不附带成品照片。如果想约束头壳成品的风格（背景、头壳材质、假发质感、光线），把你自己的 PNG 放到 `ref/` 目录（已加入 gitignore）：

| 文件 | 用途 |
| --- | --- |
| `ref/product-reference.png` | 头壳正视图 |
| `ref/turnaround-reference.png` | 头壳四视图 |

这张成品照只用来参考头壳成品的质感（哑光壳面、镜片眼、假发、布光），角色本身完全按设计稿来。最好是头壳加假发的正面特写，背景干净，不带支架、文字和水印；路径可以用 `CODEX_PRODUCT_REFERENCE_PATH` 改。如果设置 `HEAD_SHELL_EDIT_STYLE_PHOTO=true`，会改成以这张照片为底图去改成你的角色：更像实拍，但脸会往照片里那个角色偏。

## Docker

```powershell
Copy-Item .env.example .env   # 先把所有 change-me-* 换成你自己的值
docker compose up --build
```

前端在 <http://localhost:15173>，API 在 <http://localhost:18000/health>。

如果要用命令行后端，需要挂载已登录的配置目录：

- Codex：把 `%USERPROFILE%\.codex` 复制到 `runtime\codex-home`；Linux 上设置 `CODEX_CONFIG_DIR`。
- Claude Code：把 `%USERPROFILE%\.claude` 复制到 `runtime\claude-home`。macOS 的登录信息存在钥匙串里，直接复制目录无效，请从 Linux 或 Windows 机器复制。

## 部署

在服务器上准备生产用的 `.env`，至少设置：

- `APP_ENV=production`
- `ALLOW_FIXTURE_GENERATION=false`
- `POSTGRES_PASSWORD`、`MINIO_ROOT_PASSWORD`、`JWT_SECRET`、`ADMIN_AUDIT_PASSWORD` 用强密码
- `CORS_ALLOWED_ORIGINS`
- 你用的后端和对应的 Key

然后部署当前提交：

```powershell
.\scripts\deploy-ssh.ps1 -KeyPath "$env:USERPROFILE\.ssh\id_ed25519" -SshTarget "deploy@example.com" -RemoteAppDir "/opt/vsk"
```

脚本会上传 `git archive`，检查生产环境没有开启测试样例生成，然后重建容器。

不要提交 `.env` 和 `runtime/settings-overrides.json`，两者都可能含有 Key。

## 开发

```powershell
# 前端
cd frontend; npm install; npm run dev; npm test

# 后端
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
.\.venv\Scripts\python -m pytest
```

设计文档在 [docs/plans](docs/plans/)，当前进度和实现细节在 [docs/handover.md](docs/handover.md)。

## 许可证

GPL-3.0-or-later，沿用上游 KigCraft 的许可证，详见 [LICENSE](LICENSE)。
