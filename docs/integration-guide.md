# V.S.K 技术栈整合指南

状态：执行中（第四阶段已完成；审计 7.4.1–7.4.4 已修复，7.5 测试缺口已补；Nginx/Docker 运行时待有环境复核）
日期：2026-11-07
范围：前端、后端、Docker 部署、Blender 参数化链路、配置与文档

## 1. 目标

在不改变现有产品功能的前提下，减少技术栈中的重复衔接和历史遗留，让项目从“保留多套未完成架构”收敛为一套清晰、可维护的实际架构：

```text
React + Vite 前端
FastAPI 单进程后端
asyncio 进程内任务队列
SQLite + JSON 文件 + runtime/ 文件系统
Blender 无头子进程
```

整合后的目标状态：

- 本地开发与 Docker 部署使用同一套真实依赖。
- 没有未使用的数据库、缓存、对象存储和空转 worker。
- 前端只保留一套图标体系，并逐步收敛到一套样式体系。
- Blender 参数契约只保留当前实现使用的一条链路。
- README、`.env.example`、部署脚本和代码行为一致。

## 2. 整合原则

1. **先删死的，再合并活的**：先移除零引用代码和依赖，再调整仍在使用的模块。
2. **每阶段可验证、可回滚**：每一批改动单独提交，完成后运行对应测试。
3. **不为了抽象而抽象**：当前单机规模下，SQLite/JSON/文件系统比 Postgres/Redis/MinIO 更合适。
4. **不把两套不同任务强行融合**：图像生成任务与 Blender 子进程任务保持独立。
5. **文档跟随代码**：删除某个配置或服务时，同步删除 README、`.env.example` 和部署脚本中的要求。

## 3. 当前实际架构

### 3.1 前端

当前前端实际使用：

- React 19 + TypeScript + Vite；
- React Router：页面路由；
- TanStack Query：请求缓存、任务轮询；
- Zod：API 响应边界校验；
- Pixi.js：编辑器网格形变；
- MediaPipe + ONNX Runtime Web：浏览器端关键点检测；
- MUI + Emotion：主要存在于 `src/ui/mui.tsx` 包装层；
- 手写 `ide.css` + Tailwind：较新的 IDE 风格页面。

### 3.2 后端

当前后端实际运行形态：

- FastAPI 单进程 API；
- 图像生成任务由 `app/generation/queue.py` 在 API 进程内调度；
- 生成任务与审计数据写入 SQLite；
- Agent 会话、设置覆盖、Blender 任务状态写入 JSON 文件；
- 图片与 Blender 产物写入 `runtime/` 文件系统；
- Blender 构建由 `BlenderJobManager` 启动可取消的无头子进程。

### 3.3 Docker

当前 `docker-compose.yml` 保留了 5 个服务，但代码真正需要的只有：

- `api`；
- `frontend`。

Postgres、Redis、MinIO 和 worker 没有实际业务消费方。

## 4. 不建议动的部分

以下内容虽然看起来依赖较重，但承担真实功能，不应在本次整合中删除。

### 4.1 前端核心依赖

保留：

- `pixi.js`：编辑器形变核心；
- `@mediapipe/tasks-vision`、`onnxruntime-web`：关键点检测；
- `zod`：前后端边界校验；
- `@tanstack/react-query`：查询缓存与任务轮询；
- `react-router-dom`：路由；
- `i18next`、`react-i18next`：多语言；
- `@tabler/icons-react`：统一图标库。

可选优化但不属于本次清理重点：

- `animeLandmarkDetector.ts` 可改为动态导入，让首屏不加载 ONNX/MediaPipe 的 JS 胶水层。

### 4.2 后端核心依赖

保留：

- `fastapi`、`uvicorn[standard]`；
- `pydantic-settings`；
- `httpx`；
- `python-multipart`；
- `pillow`。

### 4.3 Blender 核心链路

保留：

- `blender/params/schema.json` 作为参数事实源；
- `shell_builder/sizing.py` 作为共享校验和尺寸推导；
- `build_shell.py` 作为无头构建入口；
- `apply.py`、`render.py`、`export.py` 的现有职责拆分；
- 后端独立的 `BlenderJobManager`。

不建议把 Blender 构建合并进图像生成的 `job_store` / `queue`。两者虽然都有“任务、进度、状态”，但一个处理可终止子进程和模型文件，另一个处理图片生成和 SSE，强行融合会增加耦合。

## 5. 第一阶段：删除零引用依赖和死代码

这一阶段收益最大、风险最低，应优先执行。

### 5.1 删除前端未使用依赖

从 `frontend/package.json` 删除：

- `@mui/icons-material`；
- `konva`；
- `react-konva`；
- `zustand`；
- `motion`。

预期结果：

- runtime 依赖从 19 个减少到 14 个；
- 图标体系统一为 `@tabler/icons-react`；
- 安装体积与依赖审计面下降。

验证：

```powershell
cd frontend
npm install
npm test
npm run build
```

### 5.2 统一包管理器

删除：

- `frontend/pnpm-lock.yaml`；
- `frontend/pnpm-workspace.yaml`。

保留：

- `frontend/package-lock.json`。

原因：

- README、`scripts/start-local.ps1` 和 `frontend/Dockerfile` 均使用 npm；
- pnpm 工件没有实际工具链支持；
- 两套 lock 会造成版本漂移。

验证：

```powershell
cd frontend
npm ci
npm test
npm run build
```

### 5.3 删除后端未使用的数据库模块

删除目录：

- `backend/app/db/`。

从 `backend/pyproject.toml` 删除：

- `sqlalchemy[asyncio]`；
- `asyncpg`；
- `alembic`；
- `redis`；
- `boto3`；
- `pyjwt`。

原因：

- `backend/app/db/` 没有业务调用方（唯一引用是 `models.py` 自己引 `base.py`）；
- 其他依赖没有任何导入；
- 当前任务持久化使用 `job_store.py` 中的 SQLite 手写实现，不走 SQLAlchemy。

验证：

```powershell
cd backend
.\.venv\Scripts\python -m pip install -e .[dev]
.\.venv\Scripts\python -m pytest
```

### 5.4 删除 Docker 中的未使用服务

从 `docker-compose.yml` 删除：

- `postgres` 服务；
- `redis` 服务；
- `minio` 服务；
- `worker` 服务；
- `postgres-data` volume；
- `minio-data` volume；
- `api` 中对 `postgres`、`redis`、`minio` 的 `depends_on`。

同时删除：

- `backend/app/generation/worker.py`。

修改：

- `scripts/deploy-ssh.ps1` 中的 `docker compose ... api worker frontend` 改为只启动 `api frontend`。
- 同一条 `up` 命令加上 `--remove-orphans`，让已部署过的服务器停掉并清理旧 compose 项目遗留的 `worker`、`postgres`、`redis`、`minio` 容器。

注意：

- `docker compose up` 默认不会停止已存在但不再出现在 compose 文件中的容器，不加 `--remove-orphans` 时这些容器会以 orphan 身份继续运行，第一阶段的资源精简在旧服务器上等于没生效。
- `--remove-orphans` 只清理容器，不会删除旧数据卷。`postgres-data`、`minio-data` 是否需要清理由部署者单独决定，脚本不要加 `-v` 或 `--volumes`。

原因：

- API 进程内队列已经实际承担任务执行；
- `worker.py` 只是空转循环；
- Postgres/Redis/MinIO 无代码消费方。

验证：

```powershell
docker compose up --build
```

再检查：

- 前端可以打开；
- API `/health` 正常；
- 生成任务仍可排队、执行、取消；
- Blender 任务仍可创建、取消和下载产物。

> 注：本机（开发机）没有 Docker，这一阶段的容器验证只能做静态核对（compose 文件结构、镜像与依赖关系、`Dockerfile` 入口），运行时验证需在具备 Docker 的机器上补做。

### 5.5 删除未使用的生成事件总线

删除：

- `backend/app/generation/events.py`。

同步删除：

- `backend/tests/conftest.py` 中对 `clear_events` 的导入和调用。

原因：

- 真实事件持久化走 `job_store` 和 SQLite；
- 该模块只有测试在清理，没有业务消费方。

注意：

- 两处必须同一次改动完成；先删模块后改 `conftest.py` 会让后端测试在 import 阶段直接失败。

验证：

```powershell
cd backend
.\.venv\Scripts\python -m pytest
```

### 5.6 删除 Blender v2 兼容层

删除：

- `blender/shell_builder/vsk_head_v2.py`；
- `blender/shell_builder/vsk_eye_jacobian.json`。

从 `blender/build_shell.py` 删除：

- `snapshot.get("format") == "vsk-head"` 分支；
- `vsk_head_v2.normalize(...)`；
- `vsk_head_v2.shape_delta(...)`；
- `vsk_head_v2.to_legacy(...)`。

原因：

- 后端和前端都不会生成 `format: "vsk-head"` 的参数快照；
- 文档中引用的 `docs/vsk-head-v2.md` 不存在；
- v2 参数范围已经和当前 `schema.json` 漂移；
- 删除后只保留一条参数契约链路。

验证：

```powershell
cd backend
.\.venv\Scripts\python -m pytest tests/test_blender.py -q
```

再执行一次真实 Blender 构建，确认：

- `/api/blender/status` 正常；
- `/api/blender/schema` 正常；
- 参数校验正常；
- 构建任务可以完成并输出 PNG/GLB/STL。

## 6. 第二阶段：清理假必要配置

### 6.1 清理 `config.py`

检查并删除没有消费方的配置字段，例如：

- `redis_url`；
- `s3_*`；
- `jwt_secret`；
- `mock_sms_code`；
- `mock_output_dir`；
- `max_provider_concurrency`；
- `normal_quota_window_hours`；
- `normal_base_quota`。

注意：

- 删除前先再次全仓库搜索字段名；
- 有消费方的字段不要删；
- `Settings` 当前允许忽略旧 `.env` 中多余字段，因此旧配置不会立刻报错。
- `jwt_secret` 仅出现在 `backend/tests/test_settings.py` 的“未知字段应返回 400”样例中，删除配置字段后该用例仍然成立，无需改测试。

### 6.2 清理 `.env.example`

删除与未使用服务对应的示例配置：

- `POSTGRES_*`；
- `DATABASE_URL`；
- `REDIS_URL`；
- `MINIO_*`；
- `S3_*`；
- `JWT_SECRET`；
- 其他无消费方的 mock/配额字段。

保留真实使用的配置：

- `APP_ENV`；
- API Key；
- Agent/分析/生图后端配置；
- `ARK_*`、`SILICONFLOW_*`；
- `BLENDER_PATH`、`BLENDER_SHELL_BLEND`；
- 路径类配置；
- 实际有代码读取的限流和审计配置。

### 6.3 同步 README

修正 README 中仍然要求配置 Postgres、MinIO、JWT 的内容。

重点检查：

- `README.md` 部署章节；
- `README.en.md` 部署章节；
- Docker 章节；
- 本地开发章节。

原则：

- README 只描述当前代码真实支持的路径；
- 不再暗示存在生产 Postgres/Redis/MinIO 架构。

## 7. 第三阶段：统一 `/api` 代理行为

当前问题是：

- 前端统一请求 `/api/...`；
- Vite 开发代理会把 `/api` 前缀剥掉；
- `frontend/server.mjs`（`npm run serve` 使用的预览/生产服务器）也会把 `/api` 前缀剥掉；
- Nginx 生产代理会保留 `/api`；
- 后端为了兼容以上三种行为，把每个 router 挂载了两次。

### 7.1 推荐方案

推荐让 Vite、`server.mjs` 与 Nginx 都保留 `/api` 前缀：

1. 修改 `frontend/vite.config.ts`，删除 `/api` 代理中的 rewrite。
2. 修改 `frontend/server.mjs`，删除 `proxyApi` 中的 `/api` 前缀剥离。
3. 后端只保留 `prefix="/api"` 的 router 挂载。
4. 删除无 `/api` 前缀的兼容挂载。
5. 删除生成图片 URL 前缀的兼容分支。

好处：

- 前端代码不变，始终请求 `/api/...`；
- API 命名空间明确；
- 后端 OpenAPI 不再重复；
- 与健康检查和静态文件路径更容易区分。

### 7.2 涉及文件

- `frontend/vite.config.ts`；
- `frontend/server.mjs`；
- `frontend/nginx.conf`；
- `backend/app/main.py`。

`server.mjs` 容易漏掉：它不走 Nginx，也不属于 Vite 代理，是 `npm run serve` 时真正处理 `/api` 请求的地方。只改 Vite 会让这条运行路径直接 404，因此必须一起改。

### 7.3 验证

开发模式：

```powershell
cd frontend
npm run dev
```

预览服务器模式：

```powershell
cd frontend
npm run build
npm run serve
```

生产模式：

```powershell
docker compose up --build
```

三种模式都需要确认：

- 项目列表可加载；
- Agent 会话可收发消息；
- 生成任务状态可刷新；
- 设置页可读取和保存；
- Blender schema 和任务可加载。

> 注：本机没有 Docker，生产模式需在有 Docker 的机器上补做；开发与 `npm run serve` 模式可在本机验证。

### 7.4 第三阶段审计记录（2026-11-07）

审计提交：`83b0939 refactor(api): unify /api proxy behavior`。

结论：第三阶段主链路通过，没有发现阻断性功能回归。

- Vite 与 `server.mjs` 均不再剥离 `/api`；
- Nginx 原本就保留完整 URI，无需修改；
- 后端业务 router 只保留 `/api` 挂载；
- `/health` 继续保留在根路径，符合直接 API 健康检查约定；
- 旧 `/generated/...` 与 `/static/fixtures/...` 路径有 404 回归测试；
- 当前运行时项目数据中的图片 URL 均为 `/api/...`，没有观察到删除兼容路由造成的数据回归。

独立验证结果：

- 后端：301 passed，1 skipped；
- 前端：32 个测试文件、385 个用例全部通过；
- `tsc && vite build` 通过；
- 真实 Blender E2E 仍因未显式启用而跳过；
- 本机无 Docker，Nginx/Compose 生产链路仍需在有 Docker 的环境复核。

以下四项是第三阶段审计时确认的既有问题，现均已在后续阶段修复（各小节末尾记录了修复状态与证据）。不得把它们记录为第三阶段已完成事项。

#### 7.4.1 `StaticFiles` 的 WebP MIME 类型依赖机器配置

现象：

- 本机 `mimetypes.guess_type("kigurumi-candidate-1.webp")` 返回 `(None, None)`；
- `GET /api/static/fixtures/kigurumi-candidate-1.webp` 返回 200，但 `Content-Type` 是 `text/plain; charset=utf-8`；
- 图片字节正确，生成图路由因为显式指定 `image/webp` 而没有此问题。

影响：当前浏览器通常仍能显示图片，但 fixture 静态路由与生成图路由的 MIME 契约不一致，也会影响调试、缓存和严格客户端。

修复方案：

1. 在创建 `StaticFiles` 前显式注册 `.webp -> image/webp`，不要依赖 Windows 注册表或机器级 `mimetypes` 配置；
2. 第四阶段抽取公共媒体响应逻辑时，统一检查 fixture、生成图、参考图和审计图片的 MIME 行为；
3. 为 `/api/static/fixtures/*.webp` 增加 GET 与 HEAD 的 `Content-Type` 回归测试，不能只断言状态码。

验收标准：fixture WebP 与 generated WebP 都返回 `image/webp`，图片字节保持不变。

状态：已在第四阶段修复。`backend/app/images/media.py` 在 `create_app()` 中先注册四个图片后缀的 MIME，fixture、生成图、参考图、审计图片和 Blender 产物统一复用同一份媒体类型表；`backend/tests/test_media_assets.py` 覆盖 fixture WebP 的 GET/HEAD `Content-Type`、生成图、参考图与路径穿越。

#### 7.4.2 `server.mjs` 的 SPA 回退会掩盖静态资源 404

现象：本机通过 `server.mjs` 请求 `/static/fixtures/kigurumi-candidate-1.webp` 得到 200、`text/html` 和 `index.html` 内容。该无前缀路径在第三阶段后本来就不存在，但 SPA 回退把缺失路径包装成了成功响应。

影响：当前有效 fixture URL 是 `/api/static/fixtures/...`，主链路不受影响；问题是错误的静态 URL、缺失资源或旧链接会返回 HTML 200，增加排障和错误缓存风险。

修复方案：

1. 保留 `/p/...`、`/settings`、`/blender` 等前端路由的 SPA 回退，不能全局删除回退；
2. 对带文件扩展名的缺失资源返回 404，不再回退到 `index.html`；
3. 可将 `/static/`、旧 `/generated/` 等保留路径显式排除在 SPA 回退之外；
4. 为 `server.mjs` 增加静态服务 smoke 测试，至少覆盖：前端路由返回 HTML、缺失静态资源返回 404、真实静态资源返回正确内容。

验收标准：缺失 WebP/JS/CSS 不返回 HTML 200，同时直接打开前端项目路由仍返回 `index.html`。

状态：已修复。`frontend/serverStatic.mjs` 抽出纯解析逻辑，`frontend/nginx.conf` 同步该行为：带扩展名的缺失路径返回 404，`/api`、`/static`、`/generated` 不再回退到 SPA，前端路由仍回退 `index.html`。`npm run test:server` 覆盖 8 个用例，`viteProxyConfig.test.ts` 与 `nginxConfig.test.ts`（扩展名 404、`/static`/`/generated` 404、SPA 回退保留）覆盖两条服务路径。

#### 7.4.3 Nginx 裸 `/api` 与其他服务器行为不一致

审计时现状（该问题现已修复，见本节末尾状态）：

- `server.mjs` 显式代理裸 `/api`；
- Nginx 只有 `location /api/`，不匹配裸 `/api`；
- 裸 `/api` 在 Nginx 下会进入 SPA 的 `location /`；
- 前端实际构造的请求永远是 `/api/...`，因此当前功能不受影响。

修复方案（低优先级）：

- 增加 `location = /api`，将其代理到后端，或明确返回 404；
- 目标是避免裸 `/api` 返回 SPA HTML，不要求前端新增任何请求；
- 在 `frontend/src/nginxConfig.test.ts` 中增加配置回归断言，并在真实 Nginx 环境补一次 smoke。

状态：已修复。`frontend/nginx.conf` 增加 `location = /api`，并把 `/api/` 改为 `^~` 前缀，避免 `/api/generated/*.webp` 被静态资源正则截获；`nginxConfig.test.ts` 断言精确块、代理目标、`^~` 保护、Host/X-Forwarded-* 与 1800s 超时。真实 Nginx 仍需在有 Docker 的环境跑 `nginx -t` 与一次请求。

#### 7.4.4 Ruff 告警数量必须按检查范围记录

“约 21 条告警”只对应 `backend/tests/`，不是整个后端的总数。本机使用 `--no-cache --statistics` 得到的基线为：

| 检查范围 | 数量 | 主要规则 |
| --- | ---: | --- |
| `backend/app/` | 90 | ISC004、TRY004、I001、B008 等 |
| `backend/tests/` | 21 | F811、I001、F401 等 |
| `backend/` 全量 | 117 | 上述合计及其他少量规则 |

这些告警不是第三阶段代理修改引入的，但也不应把 21 写成全仓告警总数。统计会受 Ruff 规则配置影响，后续应在 `pyproject.toml` 明确项目规则集，并以同一命令作为 CI 基线。

修复原则：

- 单独安排 lint 清理提交，不与代理或业务重构混合；
- 自动修复与手工修复分开；
- 不使用全仓 blanket ignore 掩盖问题；
- FastAPI 的 `File`、`Depends` 相关 B008 告警应按框架语义判断，不机械修改函数签名；
- 每批清理后运行完整 pytest。

状态：已清理。`backend/.venv/Scripts/python.exe -m ruff check .` 全量通过：`pyproject.toml` 用 `extend-immutable-calls` 处理 FastAPI B008，安全自动修复 I001/UP035/UP041，手工处理 ISC004/RUF046/PIE810/FLY002；TRY004 与 BLE001 逐条判断后以带理由的行内 noqa 保留原有异常语义；同期 `pytest` 310 passed, 1 skipped。

### 7.5 第三阶段后续测试缺口

以下属于测试覆盖缺口，不是已证实的功能回归：

1. `server.mjs`、Vite 和 Nginx 的“保留 `/api` 前缀”主要依赖手工 smoke，缺少自动化代理回归测试；
2. `.vkp` 测试使用 fixture 项目，未覆盖包含真实 generated 文件的导入后 HTTP 图片加载；
3. 建议补一个含 generated 图片的 `.vkp` 往返用例，直接请求导入后的 `state.images[].url`，防止未来路径调整破坏项目迁移。

状态：已补齐。1) Vite 与 `server.mjs` 由 `viteProxyConfig.test.ts`、`serverStatic.test.mjs` 覆盖，Nginx 由 `nginxConfig.test.ts` 覆盖；2) `test_export_import_carries_generated_outputs` 以真实 `runtime/generated` 输出往返 `.vkp`，逐字节校验导出包与导入后 `state.images[].url` 的 HTTP 响应。

## 8. 第四阶段：合并重复实现

### 8.1 抽取公共图片响应逻辑

当前图片媒体类型和安全路径解析在多个模块中重复，建议抽到：

- `backend/app/images/`；或
- `backend/app/core/`。

需要合并的重复内容：

- `IMAGE_MEDIA_TYPES`；
- 防止 `..` 和绝对路径的路径解析；
- 图片后缀白名单；
- 文件不存在时统一返回 404。

涉及：

- `backend/app/main.py`；
- `backend/app/audit/router.py`；
- `backend/app/references/router.py`；
- `backend/app/blender/service.py`。

### 8.2 处理 album 的内存持久化

当前相册数据是纯内存，重启后丢失，与其他模块的落盘约定不一致。

可选方案：

1. 保持易失，但在 API 和文档中明确；
2. 改为 JSON 文件，与 Agent 会话和设置覆盖保持一致；
3. 后续如果相册成为正式功能，再考虑统一进 SQLite。

本阶段建议先做方案 1，避免把产品决策混进技术整合。

### 8.3 收敛 Blender 契约双写

建议处理以下项目：

1. 用后端 `/api/blender/validate` 响应初始化前端参数，减少前端 `defaultParams()` 与后端 `sizing.defaults()` 的双写。
2. 对齐前端 `derivedSchema` 与后端 `sizing.resolve()` 返回字段。
3. 把 Blender 构建日志 `log_tail` 暴露给 API，并在任务失败时显示，或者停止采集。
4. 清理 `schema.json` 中无消费方的注解字段。
5. 删除或修正未实际消费的 `structure.neck_opening_mm` 参数。
6. 给预设文件的 `schema_version` 增加校验，避免 schema 升级后静默过期。
7. 将版本警告文案提取到 `sizing.py` 中，避免后端和构建脚本各写一份。

验证：

```powershell
cd backend
.\.venv\Scripts\python -m pytest tests/test_blender.py -q

cd ..\frontend
npm test -- BlenderPage
npm run build
```

## 9. 第五阶段：收敛前端样式体系（可选，单独项目）

这一阶段工作量最大，不建议与第一至第四阶段混在一起。

### 9.1 当前问题

前端同时存在三套样式体系：

- MUI + Emotion；
- 手写 `ide.css` 设计系统；
- Tailwind 工具类。

其中 MUI 主要集中在 `src/ui/mui.tsx` 包装层后，形成一套历史迁移遗留的翻译层。

### 9.2 推荐方向

长期目标：

```text
手写 ide.css 设计系统
+ Tailwind 工具类
+ Tabler 图标
```

最终删除：

- `src/ui/mui.tsx`；
- `src/app/theme.ts` 中 MUI 主题；
- `ThemeProvider` 和 `CssBaseline`；
- `@mui/material`；
- `@emotion/react`；
- `@emotion/styled`。

### 9.3 推荐迁移顺序

1. 迁移低复杂度页面：
   - `LanguageSelector`；
   - `AdminAuditPage`；
   - `AuditOverviewCharts`。
2. 迁移设置页和项目页中的剩余 MUI 用法。
3. 最后迁移编辑器控件：
   - `ParameterSlider`；
   - `FaceControls`；
   - `EyeControls`；
   - `BrowControls`；
   - `MouthControls`；
   - `LiquifyControls`；
   - `EditorToolbar`；
   - `EditorToolRail`。
4. 删除 MUI Provider、主题和包装层。

### 9.4 风险

编辑器控件承担核心交互，尤其是：

- Slider；
- Tooltip；
- Tabs；
- Menu；
- 键盘导航；
- ARIA 可访问性。

每次迁移都必须运行：

```powershell
cd frontend
npm test
npm run build
```

并通过真实页面检查编辑器的拖动手感、提示框、菜单和深浅色主题。

## 10. 文档同步

完成第一至第四阶段后，需要同步这些文档：

- `README.md`；
- `README.en.md`；
- `.env.example`；
- `scripts/deploy-ssh.ps1` 相关注释；
- `docs/plans/blender-procedural.md`；
- `backend/app/blender/__init__.py` 中的计划文档引用。

特别注意：

`docs/plans/blender-procedural.md` 当前描述的是较早的设计方案，实际实现已经改为基于基座 `.blend` 的 Pulsar 参数化链路。应重写为当前实现说明，或在文首明确标记“历史设计稿，仅作背景参考”。

## 11. 推荐提交顺序

建议按以下提交拆分，保证每次都能独立回滚：

1. `chore(frontend): remove unused runtime dependencies`
2. `chore(frontend): standardize on npm lockfile`
3. `chore(backend): remove unused database stack`
4. `chore(deploy): remove unused compose services and worker`
5. `chore(generation): remove unused in-memory event bus`（必须连同 `conftest.py` 的调用一起改）
6. `chore(blender): remove unused v2 parameter compatibility layer`
7. `chore(config): remove settings with no consumers`
8. `docs: align setup and deployment docs with actual stack`
9. `refactor(api): unify /api proxy behavior`
10. `refactor(images): extract shared media response helpers`
11. `refactor(blender): align parameter contracts`
12. `refactor(ui): replace MUI shim with ide components`（可选，长期）

## 12. 总体验收标准

整合完成后应满足：

- `frontend/package.json` 没有零引用 runtime 依赖；
- 前端只保留 npm 锁文件；
- `backend/pyproject.toml` 只保留真实导入的依赖；
- `docker-compose.yml` 只启动 `api` 和 `frontend`；
- 后端没有空的 worker；
- Blender 参数链路只有一套当前使用的 schema；
- README 与 `.env.example` 不再要求配置不存在的服务；
- dev 和 prod 的 `/api` 代理行为一致；
- 前端测试全部通过；
- 后端测试全部通过；
- Docker 构建和启动通过；
- 真实生成链路可用；
- 真实 Blender 构建链路可用。

## 13. 最终验证命令

前端：

```powershell
cd frontend
npm ci
npm test
npm run build
```

后端：

```powershell
cd backend
.\.venv\Scripts\python -m pip install -e .[dev]
.\.venv\Scripts\python -m pytest
```

Docker：

```powershell
docker compose build
docker compose up
```

手工验证：

- 打开前端首页；
- 创建或打开一个项目；
- 上传参考图；
- 触发一次 Agent 分析和生成；
- 保存一次手动编辑版本；
- 打开设置页并保存一项设置；
- 打开 `/blender`；
- 创建一次 Blender 构建；
- 下载 PNG、GLB 或 STL；
- 重启服务后确认项目、版本、Blender 历史仍可读取。

## 14. 推荐结论

本次整合不需要引入新的框架，也不应该为了“统一”而迁移到更大的基础设施。正确方向是把项目收敛到当前真实使用的小型架构：

```text
FastAPI + SQLite/JSON + runtime/
React + Vite + Query + Zod
Pixi + ONNX/MediaPipe
Blender headless subprocess
```

优先删除的是：

- 未使用的数据库、缓存、对象存储；
- 空转 worker；
- 未使用的前端依赖；
- 双包管理器；
- 旧 Blender 参数协议；
- 由代理不一致造成的后端双挂载；
- 与实际代码不一致的部署和配置文档。

这样可以先获得大部分收益，再把 MUI 样式迁移作为后续独立项目处理。
