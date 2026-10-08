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

