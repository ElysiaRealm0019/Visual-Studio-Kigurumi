# 手动调整（形变）软化 —— 交接报告

日期：2026-10-08
范围：前端编辑器「手动调整」相关滑块与液化笔刷的形变量级 / 过渡柔和度

> **2026-10-08 追加轮（眼睛幅度修复）见文末第 8 节。上一轮「全体减弱」的方向对眼睛是错的：**
> 用户提供了原作者未发布最新版的截图（眼睛面板：上下 = ±1.00、提肌 = -0.62，结果自然半睁眼），
> 而本轮把眼睛系数补回到可见量级并保持柔和过渡。第 3.1 / 3.2 节中眼睛相关数值以第 8 节为准。

## 1. 背景与目标

用户反馈：手动编辑的图片扭曲「过于生硬」，原作者的可调整范围较小且不会出现明显的图片扭曲。随后进一步反馈「所有滑块可能都有类似问题」，要求系统性审计并统一修正。

目标：在不破坏滑块功能与既有交互契约的前提下，统一降低形变量级、柔化过渡带，使形变「可见但不拉扯」。

## 2. 形变架构速览

所有滑块最终在 `frontend/src/features/editor/deformation/pixiStage.ts` 的 `applyMeshDeformation` 中作用于 Pixi.js MeshPlane（65×89 顶点，逐顶点累加偏移），分两条管线：

1. **推挤笔触（pushStroke）** —— 脸型/比例、眉、眼细节、液化推挤。
   - 走 `strokes` 循环，falloff 用 `getBrushFalloff`（smoothstep，边缘斜率归零，天然较柔）。
   - 单笔位移 = 单位方向向量 × `falloff` × `stroke.strength` × `maxMeshDisplacementCssPixels`。
   - **问题定位：量级偏大（非过渡问题）。**

2. **仿射网格变换（affine）** —— 眼（size/height/width/distance/vertical/tilt/regionScale）、嘴（全部）。
   - 走 `eyeTransforms` / `mouthTransforms`，falloff 用 `getEyePatchFalloff` / `getMouthPatchFalloff`。
   - falloff 为 **plateau 型**：核心区满强度、仅外圈回落 → 核心区像「整块贴纸被拉伸」，边界处梯度有折点 → **「生硬 / 明显扭曲」的主因。**
   - **问题定位：plateau 太硬 + 缩放系数/上限偏大。**

### 滑块三级映射链（face/eye 同构）

1. UI 滑块 `ParameterSlider`：-1 ~ 1
2. 控件组件 `actualMax/actualMin` → `toActualValue`（`actualMax` × `actualMin` 线性映射；「扩大参数范围」勾选时 ×2）
3. `recipe.*ControlRanges` 钳制最终值
4. `pixiStage` 将 recipe 值转成 pushStroke 或 affine 变换

> 关键结论：**降低 recipe 范围**与**降低 pixiStage 系数**对最终网格输出是等价的（两者都是线性缩放）。因此本轮优先改 `pixiStage` 系数/常量，避免触碰 UI 范围与大量范围钳制类断言。

## 3. 已完成的改动

### 3.1 上一轮（眼睛专项 + 液化）

| 位置 | 改动 |
|---|---|
| `recipe.ts` `eyeControlRanges` | 全部 ×0.6（如 `eyeSize` 0.6→0.36、`eyeTilt` 1→0.6、`eyeDistance` ±0.1→±0.06） |
| `EyeControls.tsx` | `eyeControls` 的 `actualMax/Min` 同步 ×0.6（保持勾选扩大后 ≈ recipe 上限） |
| `pixiStage.ts` `createEyeMeshTransforms` | 缩放系数与上限下调（见 3.2 表） |
| `pixiStage.ts` `maxAccumulatedMeshDisplacementCssPixels` | 72 → 60 |
| `pixiStage.ts` `eyeAffineCorePlateau` | 0.78 → 0.5 |
| `EditorWorkspace.tsx` `defaultLiquifyWarpStrength` | 0.12 → 0.09 |

同步更新断言：`recipe.test.ts` 3 处、`EditorWorkspace.test.tsx` 3 处。

### 3.2 本轮（全滑块统一软化）

只改 `pixiStage.ts`，无需改测试。

| 常量 / 表达式 | 旧值 | 新值 |
|---|---|---|
| `maxMeshDisplacementCssPixels`（推挤全局上限，作用于脸/眉/眼细节/液化） | 84 | **60** |
| `maxAccumulatedMeshDisplacementCssPixels`（叠加位移上限） | 60 | **42** |
| `eyeAffineCorePlateau`（眼仿射过渡带） | 0.5 | **0.4** |
| `mouthAffineCorePlateau`（嘴仿射过渡带） | 0.62 | **0.38** |
| `createMouthMeshTransforms` scaleX | `1 + mouthSize*0.42 + mouthWidth*0.74`，clamp(0.55, 1.72) | `1 + mouthSize*0.32 + mouthWidth*0.52`，clamp(0.66, 1.5) |
| `createMouthMeshTransforms` scaleY | `1 + mouthSize*0.38`，clamp(0.62, 1.42) | `1 + mouthSize*0.28`，clamp(0.72, 1.3) |
| `createEyeMeshTransforms` scaleX（上一轮已改，此处登记） | `1 + eyeSize*0.3 + eyeWidth*0.22`，clamp(0.62, 1.48) | `1 + eyeSize*0.22 + eyeWidth*0.16`，clamp(0.82, 1.22) |
| `createEyeMeshTransforms` scaleY（上一轮已改，此处登记） | `1 + eyeSize*0.3 + eyeHeight*0.34`，clamp(0.58, 1.54) | `1 + eyeSize*0.22 + eyeHeight*0.26`，clamp(0.8, 1.24) |

### 3.3 明确「未改动」的契约常量（勿动）

这些是位置语义契约，改了会破坏行为与测试：

- `maxEyeDistanceTranslateCssPixels = 34`、`maxEyeVerticalTranslateCssPixels = 28`
- `maxMouthTranslateXCssPixels = 26`、`maxMouthTranslateYCssPixels = 24`、`maxMouthSmileCssPixels = 18`
- `maxLocalDisplacementScale = 96`（预览位移指标，测试断言 `<= 96` 与 `faceWidth >= 20`）

### 3.4 与本次相关但本轮未改的滑块族

- **脸型/比例**（`FaceControls.tsx`：`actualMax` 多为 0.2、`faceLength` 0.3；`recipe.faceControlRanges` 上限 0.4 / `faceLength` 0.6）：走 pushStroke，本轮由 `maxMeshDisplacementCssPixels` 全局 ×0.714 统一软化。
- **眉**（`BrowControls.tsx`：`actualMax` 全 0.3；`recipe.browControlRanges` 全 ±0.6）：同上，走 pushStroke。
- **液化**（`LiquifyControls.tsx`：半径 12~120、变形强度 0~0.25、缩放强度 ±5）：上一轮已弱化强度与位移上限。
- **细节**：`DetailControls.tsx` 返回 `null`，无滑块。
## 4. 量化效果（滑块拉满时的最大形变）

- 脸型/比例：从约 7~23px 位移降到约 5~17px（如 `vLine` 23.5→16.8px、`faceWidth` 20.8→14.9px）。
- 眉：`browVertical` 20.2→14.4px 量级。
- 眼细节：`eyeLift` 12.7→9.1px 量级。
- 嘴仿射：最大横向拉伸由 48% 降到 33%（`scaleX` 1.48→1.33，上限 1.72→1.5），纵向 13%→10%。
- 眼仿射：过渡带由 50% 核心区放到 40%，进一步柔化。

## 5. 测试与验证

- 命令：`cd frontend && npx vitest run`
- 结果：**20 个测试文件 / 155 个用例全部通过**，`GetDiagnostics` 对 `pixiStage.ts` 无告警。
- 本轮为常量/系数级修改，未触碰范围钳制与映射，故未修改任何断言。
- jsdom 限制（背景）：无 2D canvas context、`new Image()` 加载 blob URL 不触发 `onload`，因此测试基于网格顶点/笔画几何断言，非像素比对。

## 6. 待办 / 风险

1. **需要真人手感确认**（本轮改动为基于数值的推断，未经人眼验证）：
   - 脸型/比例、眉、嘴三族是否达到「可见但不拉扯」；
   - 是否存在某族偏弱（如眉）或仍偏硬（如嘴横向）。
2. 若某族仍偏硬：优先调该族的 `*AffineCorePlateau`（嘴）或个别 pushStroke 系数（脸/眉），不要再动全局常量以免连带影响其他族。
3. 若某族偏弱：回退 `maxMeshDisplacementCssPixels` 或该族系数即可，测试不受影响。
4. 无对应 plan/spec 变更；`docs/plans/pluggable-backends.md` 与本主题无关。

## 7. 关键文件索引

- `frontend/src/features/editor/deformation/pixiStage.ts` —— 全部形变数学与常量（本轮唯一改动文件）
- `frontend/src/features/editor/deformation/recipe.ts` —— 各 `*ControlRanges` 钳制上限与默认值
- `frontend/src/features/editor/components/{FaceControls,EyeControls,BrowControls,MouthControls,LiquifyControls,DetailControls}.tsx` —— 滑块 → 实际值映射
- `frontend/src/features/editor/EditorWorkspace.tsx` —— 液化默认参数、笔画创建与累积
- 测试：`pixiStage.test.ts`、`recipe.test.ts`、`featureControls.test.ts`、`EditorWorkspace.test.tsx`

## 8. 追加轮：眼睛幅度修复（2026-10-08 下午）

### 8.1 背景

用户提供了原作者（kigcraft）**未发布最新版**的眼睛面板截图作为基准：

- 上下拉到满量程 ±1.00 仍然自然；
- 提肌 -0.62 呈现明显且干净的半睁眼；
- 眼高、长度等在该面板中量级同样可观。

对照发现：上一轮「全体减弱」后，眼睛族的实际作用量级基本不可见（提肌 -0.62 仅 ~2.8px/720px 预览、大小 ±1 仅 ~4% 缩放），与截图效果「根本比不了」。**上一轮把眼睛范围和系数一起缩小的方向是错的**：原作者新版恰恰支持满量程且干净。本轮把眼睛系数恢复到可见量级，同时保留平滑过渡（无硬边/拉扯）。

### 8.2 本轮改动（只改 `pixiStage.ts`）

| 常量 / 表达式 | 旧值 | 新值 |
|---|---|---|
| `maxAccumulatedMeshDisplacementCssPixels` | 42 | **80**（为眼睛复合操作留余量；仍防撕裂） |
| `maxEyeVerticalTranslateCssPixels`（上下满量程位移） | 28 | **36**（默认滑块 ±1 ≈ ±18px/720px） |
| `eyeAffineCorePlateau` | 0.4 | **0.5**（眼核心更刚性） |
| `eyeAffineRadiusScale`（新增，仿射选区外扩） | — | **1.3**（眼睛完整落在选区内，消除眼角裁切发糊） |
| `createEyeMeshTransforms` scaleX | `1 + size*0.22 + width*0.16`，clamp(0.82,1.22) | `1 + size*0.45 + width*0.3`，clamp(0.7,1.38) |
| `createEyeMeshTransforms` scaleY | `1 + size*0.22 + height*0.26`，clamp(0.8,1.24) | `1 + size*0.45 + height*0.5`，clamp(0.7,1.38) |
| 提肌 pushStroke 强度系数 | 0.42，半径 0.9rX | **2.2，半径 0.8rX**（默认满量程 ≈ 24px 上睑位移） |
| 眼瞳大小 pushStroke 强度系数 | 0.5 | **0.85** |
| 眼睑下至 pushStroke 强度系数 | 0.4，半径 0.8rX | **1.8，半径 0.8rX** |
| 眼尾上扬 pushStroke 强度系数 | 0.42，半径 0.55rX | **1.6，半径 0.55rX** |

同步更新断言：`pixiStage.test.ts`（translateY -28→-36、scaleX ≥1.15）、`featureControls.test.ts`（新增「默认量程下眼睑/眼角笔画强度」用例）。

### 8.3 视觉验证方式（可复现）

jsdom 无法出像素，本轮用真实管线做了人眼比对：

1. 临时 Vite 页面挂 `mountPixiStage` + `detectAnimeLandmarks`（真实 HRNet 检测 + 真实 Pixi 网格形变），对同一张候选图渲染「每个滑块满量程 / 参考截图组合 / 压力测试」网格；
2. Playwright（channel=msedge，headless，SwiftShader WebGL）截图导出大图 + 2.5~3 倍眼部特写；
3. 结论：上下 ±1、提肌 -0.62/-1、大小/眼高/长度、眼距、眼睑下至、眼尾、眼瞳均达到「可见且干净」；扩展量程的极端组合仍可控。
4. 验证用临时文件（`frontend/eye-harness.html`、`frontend/src/eyeHarness.ts`、`frontend/public/harness/`）用完已删除，不入库。

### 8.4 测试

- `cd frontend && npx vitest run`：**20 个测试文件 / 156 个用例全部通过**；`npx tsc --noEmit` 通过。

### 8.5 遗留 / 待确认

1. 原作者面板第 7 项标签疑为「眼眶大小」（其描述行亦写「眼眶」），我们实现为「眼瞳大小」（`pupilSize`，虹膜径向缩放）。若确认语义应为「眼眶/开口大小」，可把该笔画中心放大、半径调大即可切换，无需改结构。
2. 嘴部仿射上一轮已减半、未恢复；若用户后续对嘴部也给出类似的「幅度不足」反馈，参照本轮思路处理（提系数、保留平滑 falloff），勿再动全局常量。
3. `ref/` 下仅有 kigurumi 实拍参考图，无原作者面板截图入库；本轮基准来自用户对话截图。


## 9. 换用原作者 V2 形变模块（2026-10-08 晚）

原作者发布了 V2 代码（本地参考：`KigCraft-2`）。第 3、8 节的手调常数已被整体替换，**以 V2 实现为准**，旧文件备份在 `runtime/v1-editor-backup/`（不入库）。

- `deformation/` 整体换成 V2：新增 `faceDeformation.ts`（脸型 + 五官保护区）、`browDeformation.ts`、`falloff.ts`；网格 65×89 → 129×177；眼睛不再用仿射 + plateau，改为分区位移模型（鼓包 / 平移 / 拉伸 / 眼睑 / 眼尾）。
- 新增 `core/imageCoordinates.ts`（recipe 可选 `imageCoordinates`，使效果与视口无关）。**我们的 EditorWorkspace 暂未接入锚定**，未设置时 pixiStage 走旧的视口单位逻辑。
- 眉毛关键点（眉头 / 眉峰 / 眉尾）可拖动（`AnnotationLayer`），未检测到时按双眼估算并以虚线显示。
- 关键点检测：多候选人脸框中选 HRNet 拟合最好的一个；眉点在眼线以下视为误检。
- recipe 键名变更：`eyeLift→eyeUpperLid`、`pupilSize→eyeIrisSize`、`lowerLid→eyeLowerLid`、`eyeTail→eyeTailLift`、`browInnerSpacing→browHeadSpacing`、`browArch→browPeak`；`smallFace` 允许负值。
- 控件：`FaceControls` 用 `group="shape" | "proportion"`；眼 / 眉 / 液化范围按 V2；液化默认强度 0.09 → 0.2。
- 标注 prompt 格式改为 `1. (x: 75.0%, y: 25.0%) 内容`（后端按自由文本处理，无影响）。
- 验证：`npx tsc --noEmit` 通过；vitest 24 个文件 / 340 个用例全部通过；真实页面中，眼睛大小 +1、提肌 -1 的效果干净。

## 10. 生成 prompt 换用 V2 的阶段描述（2026-10-08 晚）

只改了 `backend/app/generation/backends/prompting.py` 和 `backend/app/agent/runner.py`。两阶段流程（2D 设定图 → 头壳正视图 → 四视图）本来就和 V2 的「平面参考图 → 头壳渲染 → 四视图」一一对应，所以没有改流程，只换了各阶段的 prompt 文本：

- 新增共享片段：`HEAD_POSE`（正脸、不歪不转，参考图是斜角也要重建成正面）、`HEAD_SHELL_LOOK`（写实影棚产品照：85mm、柔光箱布光、树脂壳面、带厚度的透明镜面眼、假发纤维高光）、`HEAD_SHELL_PRESENTATION`（头壳悬空，无底座无脖子，头发自然垂落）、`WATERMARK_LINE`。
- 2D 设定图：按参考图原画风画，不萌化、不 3D 化；去掉兜帽/帽子等遮挡物并补全头发；只保留属于角色身份的小饰品；纯白、无阴影。
- 头壳正视图：明确是「把平面设定翻译成实物」，不是重画；眼睛改成镜面眼（替换原来的「simplified weak nose」等写法）。
- 四视图：单行，从左到右依次为正面、左前 3/4（45°）、侧面（90°）、背面；四个头等大、基线对齐；不出现文字和边框。
- Agent：分析参考图之后，如果缺少可用参考，或关键信息矛盾/被遮挡（发色、瞳色、耳朵被帽子挡住、不确定是哪个角色），先问一个简短问题；否则直接生成。
- 为满足既有测试的意图，prompt 中不出现 "stand" 一词（防止模型把支架画出来）。耳朵描述分为设定图版和头壳版。
- 篇幅：头壳正视图 prompt 在 image API 后端约 7.7k 字符（约 1260 词）。如果 SiliconFlow / 方舟对长 prompt 效果变差，优先给 image API 单独精简 `HEAD_SHELL_LOOK`。
- 测试：与 prompt、agent 和各后端相关的 9 个测试文件共 140 个用例全部通过，其余文件也逐个跑过。`test_generation_local_revision.py` 原先会卡死，已修复（见第 11 节）。

## 11. 后端测试卡死的原因（2026-10-08）

- 现象：`tests/test_generation_local_revision.py::test_local_revision_creates_front_local_revision_job` 一直挂住，整套 pytest 无法结束。
- 原因：该文件的 `make_client` 设置了 `GENERATION_PROVIDER=codex`，提交局部重绘任务后，队列（`generation_queue.submit_job` → `asyncio.create_task`）会**真的启动 `codex exec` 子进程**（本机装了 codex，有可能消耗 Codex 额度）。不在 `with` 块里使用的 `TestClient` 每个请求都会临时开一个事件循环，请求结束时取消剩余任务。此时子进程还在 `create_subprocess_exec` 连接管道，Windows 下 asyncio 取消后会一直等子进程传输层收尾，任务因此永远不结束。
- 修复：这些测试只校验任务创建，所以在 `make_client` 里把 `generation_queue.submit_job` 替换为空操作。现在整套后端测试 232 个用例 20 秒跑完。
- 注意：以后写走 HTTP 接口创建生成任务的测试，要么用 `GENERATION_PROVIDER=fixture`，要么替换掉 `submit_job`，不要让真实后端在测试里运行。

## 12. 编辑器本地草稿（2026-10-08）

参考 V2 的 `drafts/`，但按本项目单机的定位做了精简：不做云同步，不保存撤销历史。

- `frontend/src/features/editor/drafts/localDrafts.ts`：IndexedDB 库 `vsk-editor-drafts`，键为 `[projectId, versionId]`，存 `recipe`（含关键点、液化、标注）、局部生成蒙版笔画和局部生成说明。没有 IndexedDB 的环境（如 jsdom）下所有操作都不做任何事。
- `frontend/src/features/workspace/useEditorDrafts.ts`：打开版本时先读草稿，读完才挂载编辑器；编辑停止 400ms 后写入，`pagehide` 和切换版本时立即落盘；内容变回空就删除草稿；「保存为新版本」成功后删除原版本的草稿。
- `EditorWorkspace` 新增 props：`initialMaskStrokes`、`initialLocalEditNote`、`onDraftChange`；recipe 和关键点沿用原有的 `recipe`、`initialLandmarks`。导出 `recipeHasEdits`。
- 交互变化：切换或关闭标签页不再弹「放弃修改」确认框（i18n 键 `workspace.discardChanges` 已删除）；有草稿的标签页显示未保存圆点。
- 已知限制：recipe 仍未做 `imageCoordinates` 锚定（见第 9 节），液化半径、蒙版半径按预览尺寸记录，窗口大小变化后恢复的效果可能有轻微差异。V2 的工程包（`.kigcraft`）导出依赖锚定，本次未做。
- 验证：在真实浏览器中，调整滑块后刷新页面，数值和画面都恢复；复位后草稿删除、圆点消失；只填局部生成说明时也能恢复。vitest 340 个用例全部通过。

## 13. 设置页（切换后端）

- 入口：首页和工作区标题栏的齿轮按钮，路由 `/settings`。
- 可切换：对话助手 LLM（`AGENT_LLM_PROVIDER` 及 API 地址/模型）、参考图分析 LLM（`LLM_PROVIDER`）、生图后端（`IMAGE_PROVIDER` 及 SiliconFlow/Ark 模型名）。
- 后端：`GET/PUT/DELETE /api/settings`（`backend/app/settings/router.py`）。修改写入 `RUNTIME_SETTINGS_PATH`（默认 `runtime/settings-overrides.json`，已被 gitignore），`get_settings()` 把它叠加在 `.env` 之上；PUT 传 `null` 删除单项覆盖，DELETE 全部恢复为 `.env`。
- 可编辑字段白名单在 `config.py` 的 `EDITABLE_SETTINGS`，包括各后端的地址、模型名，以及 API Key（`agent_llm_api_key`、`siliconflow_api_key`、`ark_api_key`、`codex_bridge_token`）。
- API Key 只写不读（`SECRET_SETTINGS`）：GET 只返回 `secrets.{key}.set/source/hint`（是否设置、来自 .env 还是本页、末 4 位），页面输入框留空表示不改；“清除本页保存的 Key”发 `null`，回落到 .env。Key 以明文存在 `runtime/settings-overrides.json`，**发布/打包前要连同 .env 一起清掉**。
- 页面同时显示各后端是否就绪（key 是否存在、`codex`/`claude` 命令是否可执行）。
- `APP_ENV=production` 时只读（PUT/DELETE 返回 403）；`fixture` 选项只在允许测试样例时出现。
- 测试：`tests/conftest.py` 的 autouse fixture 把 `RUNTIME_SETTINGS_PATH` 指到临时目录，避免本机保存的设置影响测试。
- 参考图分析新增 `LLM_PROVIDER=openai_compatible`（`backend/app/generation/backends/openai_compatible.py`）：安全检查和细节分析各发一次 `/chat/completions`，参考图缩到 `ANALYSIS_LLM_MAX_IMAGE_SIDE`（默认 1536）后以 base64 `image_url` 附上，模型必须支持图片输入。`ANALYSIS_LLM_BASE_URL/API_KEY/MODEL` 留空时沿用对话助手的地址、Key、模型（地址等于方舟地址时也会沿用 `ARK_API_KEY`），请求体合并 `AGENT_LLM_EXTRA_BODY`。尚未用真实参考图跑过，豆包 seed 2.0 pro 是否稳定输出要求的 JSON 需要实测。
- OpenAI 兼容请求统一走 `backend/app/core/chat_api.py`（对话助手和参考图分析共用）。`AGENT_LLM_EXTRA_BODY`（默认关闭豆包深度思考）是厂商专属字段：返回 400 且错误里提到其中某个字段时，去掉额外字段重发一次，并在内存里记住该"地址+模型"，之后直接不带（重启或测试连接时清空）。实测 GLM-5.3-flash 会拒绝 `thinking.type=disabled`。
- 设置页"额外请求参数（JSON）"可编辑（必须是 JSON 对象或留空）。"测试连接"（`POST /api/settings/probe/{agent|analysis}`，`backend/app/settings/probe.py`）用已保存的设置各发一个小请求：对话助手检查连接、额外参数、工具调用（要求模型调用 `ping` 工具）；分析检查连接、额外参数、看图（发一张红色方块问颜色）。失败按 401/403、404、连不上、不支持图片分类提示。生产环境禁用。
- 页面里的确认/输入改用应用内弹窗（`frontend/src/ui/IdeDialog.tsx`），因为部分内嵌浏览器会静默屏蔽 `window.confirm`/`prompt`，导致删除、重命名项目没反应。


## 14. 头壳生成：参考图顺序与 prompt 结构（2026-10-08）

- 问题：头壳正视图基本是 2D 设计稿加了点立体感，不像实物头壳。
- 原因对照 V2（`app/conversation/stages.py`、`style_references.py`）：
  - V2 先放用户的图，应用自带的风格照放最后，并用一段文字说明"最后的图是应用加的，只参考材质和布光"。我们原来把成品照放第一张，而 codex 拿不到文件名，prompt 里写的"商成品参考图.png"无从对应。
  - V2 的阶段说明在前、用户数据在后；我们原来把一长串细节锁定（十字瞳孔、发丝等描述画面的词）放在任务说明前面，模型照着重画插画。
- 改动：
  - `_existing_codex_image_paths` 和 `tools/codex_bridge.py`：用户图在前，成品照在后。
  - `_reference_instruction_for_mode`（头壳阶段）按位置说明各张图；成品照里的支架、水印等忽略（`STYLE_PHOTO_IGNORE`）。
  - codex prompt 顺序改为：图片说明 → 阶段说明（实物化、Look、Presentation）→ 约束 → 设计事实（细节锁定，标题改为"保留设计，用实物材质呈现"）→ 用户备注。
  - 新增 `PHYSICAL_TRANSLATION_LINE`：逐项说明画面元素变成什么实物（壳、镜片眼、假发、饰品）。
  - `job_store.py` 的约束和标题不再说 "design preview"，改为"实物头壳棚拍照片"。
- 待办：`ref/product-reference.png` 带支架和闲鱼水印（已由第 16 节的裁剪版替代）。

## 15. 对话卡"正在思考"、回复的重新生成/删除（2026-10-08）

- 卡住原因：`EventSource` 断线后会自己重连，但重连时收到错误响应（例如后端重启期间 Vite 代理返回 500/502）就会永久关闭，页面再也收不到"运行结束"，只能手动刷新。
- 修复：
  - `subscribeToEvents`：连接被浏览器放弃后 3 秒重新打开，从最后收到的 seq 继续，重连成功后补拉一次快照。
  - `useConversation`：运行中每 8 秒拉一次快照兜底。快照的 `last_seq` 比页面已有的旧时丢弃，避免慢请求把运行状态改回"进行中"。
- 助手回复下方的按钮：
  - 删除（每条回复都有）：`DELETE /api/agent/conversations/{id}/messages/{seq}`。事件不删除，只标 `deleted`，所以 seq 不会被重复使用。模型上下文里的对应消息会去掉；如果这条消息带工具调用，只清空文字，保持调用和结果成对。
  - 重新生成（只在最后一条回复上）：`POST /api/agent/conversations/{id}/regenerate`。模型上下文退回到最后一条用户消息，隐藏这一轮的回复和错误，然后重新运行。已生成的图片保留。
  - 后端逻辑在 `backend/app/agent/history.py`。

## 16. 头壳正视图的写实程度（2026-10-08）

- 对比实验（脚本 `runtime/_abtest*.py`，结果和提示词在 `runtime/abtest-ark/`）：
  - Codex（gpt-image）：不论提示词怎么调、成品照怎么换，都会照抄 2D 设计稿的画法。
  - 方舟（Seedream）：明显更像实物。
  - 以成品照为底图改成角色（编辑模式）：最像实拍，但脸会往真人那边偏；加了约束以后，又会往"照片里那个角色"的脸型和表情偏。
  - 设计稿在前、成品照只作质感参考（B）：最贴近设计稿的风格，用户选了这个。
- 默认用 B。编辑模式保留为开关 `HEAD_SHELL_EDIT_STYLE_PHOTO`（默认 false），由 `prompting.edits_style_photo()` 判断，只作用于 `front_design`：
  - 方舟和 SiliconFlow：成品照按出图比例补边后作为 Image 1（`style_photo_base`），设计稿作为 Image 2。
  - Codex 直连：成品照在前，提示词换成 `FINAL_KIGURUMI_FRONT_EDIT_PROMPT`。
  - Codex bridge 不支持编辑模式。
- 提示词的写实只做到材质这一层：
  - `ANIME_FACE_LINE`：脸是"把设计稿的动漫脸做成实物"——眼睛的大小和位置按原画，鼻子只是一个小点，嘴是一条画上去的线；不要嘴唇、鼻孔、颧骨，不能像真人。
  - `HEAD_SHELL_LOOK`：去掉鼻梁、脸颊、嘴唇的雕塑起伏；壳面改为全哑光，只有镜片眼有光泽。
- 成品照：本地 `.env` 已改为裁剪版 `ref/product-reference-head.png`（只保留头部和假发，去掉支架和水印）。
