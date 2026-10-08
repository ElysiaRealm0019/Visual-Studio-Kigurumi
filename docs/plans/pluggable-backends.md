# 计划：可插拔生成后端（LLM: Codex / Claude Code，图片: Codex / SiliconFlow）

## 目标

- 摆脱对 Codex 的硬依赖：视觉 LLM 环节（参考图安全检查、细节分析）可选 **Codex** 或 **Claude Code**。
- 图片生成环节（正面图、四视图、局部重绘）可选 **Codex（gpt-image）** 或 **SiliconFlow API（Qwen-Image-Edit-2509）**。
- 两个维度独立组合，例如 `claude_code + siliconflow` 可以完全不装 Codex。
- 现有 `GENERATION_PROVIDER=codex / codex_bridge / fixture` 配置继续可用，行为不变。

## 现状（改造依据）

- `backend/app/generation/provider.py`（约 1600 行）中，`CodexImageProvider` 同时实现了 `analyze_reference_details`（两次 `codex exec`：安全检查 + 细节分析）和 `generate_incremental`（`codex exec` → agent 调 gpt-image → 写 `manifest.json`）。
- router 和 queue 只依赖 `ImageGenerationProvider` 的两个方法加上 `name`，入口是 `get_generation_provider()`。**因此只要对外接口不变，router、queue、worker、前端都不用改。**
- 跟 Codex 耦合的外围逻辑：`ensure_codex_usage_allows_generation`（router 中 3 处）、`request_generation_cancel`（仅 bridge）、`Dockerfile` 安装 `@openai/codex`。

## 目标架构

```
backend/app/generation/
  provider.py                  # 瘦身：保留对外类型和 get_generation_provider()，从下面模块 re-export，保证旧 import 和测试不受影响
  backends/
    types.py                   # ProviderOutput / ProviderUsage / ReferenceRejectedError / ReferenceSafetyResult
    composite.py               # CompositeProvider(llm, image)：实现 ImageGenerationProvider，分别转发两类调用
    outputs.py                 # 公共产物处理：落盘、加水印、复制到 public 目录、局部重绘合成
    llm/
      base.py                  # VisionLLMBackend: run(prompt, image_paths, workspace, purpose) -> (text, TokenUsage|None)
      analysis.py              # 与后端无关的安全检查 / 细节分析流程（prompt、JSON 解析、拒绝逻辑）
      codex.py                 # 现有 codex exec 调用迁移过来
      claude_code.py           # 新增
    image/
      base.py                  # ImageBackend: generate_incremental(job_id, payload)
      codex.py                 # 现有 Codex CLI 出图 + codex_bridge 迁移过来
      siliconflow.py           # 新增
      siliconflow_prompt.py    # 新增：给扩散/编辑模型用的纯画面描述 prompt
```

`get_generation_provider()` 的逻辑：
- `fixture/mock` → 保持原样；
- 否则读取 `LLM_PROVIDER` 和 `IMAGE_PROVIDER`，组装成 `CompositeProvider`。`name` 取 `"{llm}+{image}"`，仅用于日志和审计。
- 未设置新变量时由 `GENERATION_PROVIDER` 推导：`codex` → (codex, codex)，`codex_bridge` → (codex, codex_bridge)。

> **实际落地（阶段 1 已完成）：** 结构做了简化。Codex 的 LLM 和出图逻辑耦合较深，测试也直接 patch 模块内部，所以保留在同一个文件 `backends/codex.py` 里，没有再拆成 `llm/` 和 `image/`。现在的结构是：
> `backends/types.py`（公共类型，以及能力标记 `is_fixture`、`uses_codex`、`supports_local_revision`、`uses_codex_bridge`）、`backends/analysis.py`（安全检查和细节分析用的 prompt 及解析）、`backends/prompting.py`（出图 prompt 素材）、`backends/common.py`（路径、尺寸、landmark 工具函数）、`backends/fixture.py`、`backends/codex.py`；`provider.py` 只负责工厂、`CompositeProvider` 和 re-export。
> router 不再判断 `provider.name`，改为检查上述能力标记。新后端按 `backends/claude_code.py`、`backends/siliconflow.py` 平铺添加。

## 配置项（`config.py` + `.env.example`）

```dotenv
LLM_PROVIDER=                       # codex | claude_code；留空时由 GENERATION_PROVIDER 推导
IMAGE_PROVIDER=                     # codex | codex_bridge | siliconflow；留空时由 GENERATION_PROVIDER 推导

# Claude Code
CLAUDE_CODE_PATH=claude
CLAUDE_CODE_MODEL=                  # 留空使用 CLI 默认模型
CLAUDE_CODE_CONFIG_DIR=./runtime/claude-home   # 挂载到容器 /root/.claude；也可以改用 ANTHROPIC_API_KEY
CLAUDE_CODE_TIMEOUT_SECONDS=240

# SiliconFlow
SILICONFLOW_API_KEY=
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_IMAGE_MODEL=Qwen/Qwen-Image-Edit-2509
SILICONFLOW_NUM_INFERENCE_STEPS=50
SILICONFLOW_CFG=4.0
SILICONFLOW_NEGATIVE_PROMPT=
SILICONFLOW_TIMEOUT_SECONDS=300
SILICONFLOW_MAX_RETRIES=3
SILICONFLOW_MAX_INPUT_SIDE=2048     # 参考图上传前的最长边上限，用来控制 base64 体积
```

现有 `codex_detail_analysis_model` 等配置保留，归属 Codex LLM 后端。

## 阶段 0：SiliconFlow 实测（先做，避免方案建立在假设上）

写一个独立脚本 `tools/siliconflow_probe.py`，用你自己的 API key 跑几次真实请求，确认以下几点：

1. Qwen-Image-Edit-2509 **输出尺寸规则**：是跟随 `image`（第一张图）的尺寸，还是固定档位？
2. 多图 prompt 的写法：用“图1/图2/图3”还是 “image 1/2/3” 指代效果更好？中文和英文 prompt 有没有差异？
3. 拿 1～2 个真实角色参考图，加上 `ref/product-reference.png`，看正面图效果，以及耗时和失败率。
4. 四视图：以 3:2 比例输入时实际输出多大，放大到 3000×2000 后效果能否接受。
5. prompt 长度上限（文档未写明）。

实测结果决定阶段 3 里“尺寸处理”和“prompt 模板”的具体写法。

## 阶段 1：重构，行为零变化

1. 按上面的目录结构拆分 `provider.py`。Codex 相关代码**原样迁移**，不改逻辑。
2. 引入 `CompositeProvider` 和新的工厂函数，由 `GENERATION_PROVIDER` 推导出 (codex, codex)。
3. `provider.py` 保留 re-export，保证 `tests/` 里现有的 import 和 monkeypatch 路径有效。如果某些测试 patch 的是模块内部的私有函数，就同步修改测试的 patch 路径，但不改断言。
4. 验收：`pytest` 全部通过；用 `fixture` 跑一遍 e2e。

## 阶段 2：Claude Code LLM 后端

调用方式（headless 模式）：

```
claude -p "<prompt>" --output-format json [--model <m>]
       --allowedTools Read
       --disallowedTools Bash,Edit,Write,WebFetch,WebSearch
```

- `cwd` 设为分析用的 workspace。把参考图复制成 `workspace/refs/ref-1.png …`，prompt 里写明“请用 Read 读取以下图片：refs/ref-1.png …”。Claude Code 没有 `--image` 参数，通过 Read 工具看图。
- 从 stdout 的 JSON 里读取 `result`（模型输出文本）、`is_error` 和 `usage`，把 `usage` 映射为 `TokenUsage`（`input_tokens`、`output_tokens`、`cache_read_input_tokens` → `cached_input_tokens`）。
- 安全约束：用 allowedTools 和 disallowedTools 白名单只放行 Read。另外对应现有的 `_ensure_codex_events_do_not_use_disallowed_tools`，检查输出里的 `permission_denials`，非空就记录告警。
- 复用 `llm/analysis.py` 里的 prompt 和 JSON 解析；`REFERENCE_SAFETY_PROMPT` 和 `DETAIL_ANALYSIS_PROMPT` 不改。
- Docker：`Dockerfile` 增加 `npm install -g @anthropic-ai/claude-code`；`docker-compose.yml` 给 api 和 worker 增加 `${CLAUDE_CODE_CONFIG_DIR}:/root/.claude` 挂载。
- 测试：参照 `test_codex_provider.py` 的做法，用假的可执行脚本或 monkeypatch `create_subprocess_exec`，覆盖正常 JSON、`is_error`、非法 JSON、超时、安全拒绝这几种情况。

## 阶段 3：SiliconFlow 图片后端

### 3.1 请求

- `POST {base_url}/images/generations`。请求头：`Authorization`、`X-Enable-Watermark: 0`、`X-Trace-Id: <job_id>`。响应头里的 `x-siliconcloud-trace-id` 写入日志。
- 参考图统一转成 `data:image/png;base64,...`，最长边先缩到 `SILICONFLOW_MAX_INPUT_SIDE`。
- 每个 candidate 发一次请求。接口没有 `batch_size`，而现在 `expected_output_count` 本来就是 1。
- 重试：429、503、504 和网络超时用指数退避（例如 5s、15s、45s），其余 4xx 直接失败，并把 `message` 透传到任务错误里。
- 拿到 `images[0].url` 后**立即下载**（有效期 1 小时）→ 转成 webp → 缩放或裁剪到目标尺寸 → 写入 `codex_output_dir/<session>/<job>/candidate-N.webp` → 交给 `outputs.py` **强制加水印**。关闭平台水印后需要自行添加显式 AI 标识，所以这个后端不提供关闭水印的开关。
- 产出 `ProviderOutput`；`landmarks` 保持 `None`，与当前 `AI_OUTPUT_LANDMARKS_ENABLED=False` 一致。

### 3.2 参考图分配（最多 3 张）

| 模式 | image | image2 | image3 |
|---|---|---|---|
| front_design / front_revision | 用户正面参考（按 `reference kind` 优先选 front），pad 到 800:1100 | `ref/product-reference.png` | 其余用户参考：只有 1 张时直接用，多张时横向拼成一张 contact sheet |
| turnaround | 已确认的正面图（由用户参考里传入，待核实 payload 中对应的 key） | `ref/turnaround-reference.png` | 无 |
| front_local_revision | **mask 外接矩形加 15% 边距，从 base 中裁出的区域** | 补充参考（如果有） | 无 |

局部重绘策略：接口不支持 mask，所以只把 mask 区域裁出来发给模型编辑，返回后缩放回裁剪框大小贴回原图，再用现有的 `composite_local_edit` 按羽化 mask 合成。这样模型只看到并修改目标区域，不会动到整张图。

### 3.3 Prompt（`siliconflow_prompt.py`）

- 以现有的 `FINAL_KIGURUMI_FRONT_VIEW_PROMPT` 和 `FINAL_KIGURUMI_TURNAROUND_PROMPT` 为基础，去掉写给 agent 的部分：工具要求、保存路径、manifest、landmarks、“Generate at 800x1100”这类句子。
- 增加对多张输入图的指代（“图1是角色参考，图2是成品风格参考……”），具体写法按阶段 0 的实测结果定。
- 继续拼入 detail_lock、用户需求、用户备注和参考图描述；用户文本仍然先经过 `sanitize_user_text` 处理。
- 默认 negative prompt：`text, watermark, logo, realistic human skin, realistic human eyes, multiple characters`，可通过配置覆盖。

### 3.4 尺寸

按阶段 0 的结论处理：如果输出尺寸跟随第一张输入图，就把第一张图 pad 到目标比例（白底）；拿到结果后统一用 Pillow 缩放或裁剪到 `FRONT_OUTPUT_*` / `TURNAROUND_OUTPUT_*`。

### 3.5 测试

用 `httpx.MockTransport` 模拟：成功、429 后重试成功、503 重试耗尽、400 直接失败、下载失败。另外覆盖参考图分配规则、局部重绘的裁剪和贴回、水印必定执行。

## 阶段 4：外围适配

- `ensure_codex_usage_allows_generation`：只在 LLM 或图片后端实际用到 Codex 时才检查，否则直接跳过。
- `request_generation_cancel`：SiliconFlow 不需要远程取消，保持现在只处理 bridge 的逻辑。
- `Dockerfile`：Codex 和 Claude Code 两个 CLI 都安装；不需要的后端可以不提供认证信息。
- 审计和用量：SiliconFlow 没有 token 数据，`ProviderUsage` 不产出；可以考虑在任务事件里记录图片张数和 `timings.inference`（可选）。
- 文档：README 中英文版都更新“生成后端”章节，`.env.example` 补全新变量。

## 不在本次范围内

- 本地 SD / ComfyUI 后端（以后可以作为新的 `image/` 实现加入）。
- 在前端或管理后台配置 API key（本次只支持 `.env`）。
- SiliconFlow 的 Chat API 作为 LLM 后端。

## 已确认的决策

1. Claude Code 认证：挂载已登录的 `~/.claude` 目录（`CLAUDE_CODE_CONFIG_DIR`），不使用 API key。
2. `codex_bridge` 保留，原样迁移。
3. SiliconFlow 四视图不放大，使用模型原生输出尺寸（例如 3:2 的 1584×1056）。Codex 后端仍然是 3000×2000。

## 进度（2026-10-08）

- [x] 阶段 1：重构，现有测试全部通过
- [x] 阶段 2：Claude Code LLM 后端 + Dockerfile / compose 挂载。安全检查已实际调用过；细节分析还没用真实角色图跑过
- [x] 阶段 3：SiliconFlow 图片后端（mock 测试通过）。Qwen-Image-Edit 系列实测一直超时，Kolors / Qwen-Image 正常
- [x] 阶段 4：router 改为检查能力标记，更新 `.env.example` 和 README
- [x] 通用图片 API 层：硅基流动和火山方舟共用 `image_api.py`，各家只写请求格式
- [x] 火山方舟（Agent Plan，`doubao-seedream-5-0-pro`）实测：正视图 89.5 秒、四视图 83 秒，均成功
  - 该模型不支持 `sequential_image_generation`，已去掉
  - `2K` 会按参考图比例输出 2816×1584（16:9），所以四视图默认改为显式 `1920x1280`（3:2，约 246 万像素，按 150 AFP 计）
- [x] 四视图后处理从「裁成 3:2」改为「用背景色补边到 3:2」，避免裁掉最外侧的视角（对所有 API 后端生效）
- [x] Codex CLI 0.161 兼容：从 `CODEX_HOME/generated_images/<thread_id>/` 取工具原图，后端自己转换；实测正视图 95 秒、四视图 174 秒
- [x] 参考图缺失时 prompt 不再声称附带了参考图；`ref/turnaround-reference.png` 已用 Codex 首次四视图结果生成
- [x] 对话式前端（替换旧工作台和编辑器）：后端 `app/agent/`（编排 LLM + 工具 + SSE 事件流），前端 `AgentChatPage`
  - 编排 LLM：OpenAI 兼容（默认火山方舟 Agent Plan `doubao-seed-2-0-pro`，关闭思考）或 Claude Code
  - 正视图需用户确认才能出四视图；每轮最多 3 次生成；同一批工具中前一个失败则跳过后续
  - 生成图在加水印前保留一份不公开的干净副本，回传做参考时用它，避免水印叠加
  - 实测（Claude Code 分析 + 方舟生图 + 豆包编排）：分析 → 正视图 → 文字修改 → 按钮确认 → 四视图，全程通过
- [x] 项目 + IDE 工作区（替换上一版纯对话页）：项目列表 `/`，工作区 `/p/:id`
  - 布局：活动栏 / 侧边栏（资源管理器、工具参数）/ 画布标签页 / 右侧 Agent / 状态栏；深色默认，可切浅色
  - 编辑器：从 git 恢复原作者的网格变形 + ONNX 关键点引擎，加 `layout="ide"`（工具面板、工具栏通过 portal 渲染进壳）
  - 新参数：比例（脸长、中庭）、太阳穴、眼睛 4 项（提肌、眼瞳大小、眼睑下至、眼尾上扬）、眉毛 7 项（基于检测出的眉毛关键点）
  - 局部生成：四步面板 + 修改范围（锁定区域外 = 窄羽化，尽量保持 = 宽羽化）+ 选择已有参考
  - 版本：AI / 手动 / 局部三种来源；手动保存按原图分辨率导出；编辑器读取不带水印的源图，避免水印叠加
  - 已知：`EditorWorkspace.test.tsx` 中原作者遗留的 11 个用例本来就失败（引用已删除的功能），未处理
- [ ] 清理旧编辑器遗留：`api/client.ts` 中的生成相关函数、`public/models`、`public/mediapipe-wasm` 和 konva / pixi / onnxruntime / mediapipe 依赖
- [ ] 阶段 0：Qwen-Image-Edit 恢复后重跑 `tools/siliconflow_probe.py`
- [ ] 端到端：在 Docker 中走完整流程（如 `claude_code` + `ark`）

## ⚠️ 发布前必做

- [ ] 清空根目录 `.env` 里的 `SILICONFLOW_API_KEY`（当前是开发测试用的 key），确认它没有进入任何提交、Docker 镜像、`runtime/` 产物或日志
- [ ] 到 SiliconFlow 控制台作废或轮换这个开发 key（它曾出现在聊天记录里）
- [ ] 确认 `.env.example` 中 `SILICONFLOW_API_KEY=` 仍为空
- [ ] 清空根目录 `.env` 里的 `ARK_API_KEY`（火山方舟 Agent Plan 专属 Key），同样确认没进提交、镜像、产物或日志；`.env.example` 中保持为空
