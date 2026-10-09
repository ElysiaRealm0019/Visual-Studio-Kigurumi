# 计划：Blender 程序化建模头壳（参数契约与操控接口设计）

状态：设计稿，未实施
日期：2026-10-09

## 1. 背景与目标

V.S.K 目前的产出止步于 2D 设计稿和头壳效果图。本计划引入 **Blender 程序化建模**，把已确认的设计变成一个**参数化的 3D 头壳模型**（可预览、可导出打印）。核心问题是：Blender 侧需要暴露哪些参数、以什么契约暴露，才能让"直接操控"和"Agent 操控"两种方式都成立。

**本计划的答案（先给结论）**：

1. 定义一份**参数注册表（Parameter Schema）**作为唯一事实源——每个参数的名字、类型、范围、默认值、单位、说明、所属分组、取值来源都登记在内。
2. Blender 侧只实现一个**确定性执行器**：读 `params.json` → 构建/更新模型 → 输出预览图和模型文件。它不认识 Agent，也不认识 UI。
3. **直接操控**（参数面板，类似现有 2D 编辑器的滑块）和 **Agent 操控**（LLM 产出参数 JSON diff）都只是 Schema 的两个客户端。Agent 的输出永远是一份经过 Schema 校验和夹取的参数快照，而不是一串 Blender 命令——这保证了可校验、可重放、可撤销。

## 2. 三种操控模式的边界（回答"要不要 Agent"）

| 模式 | 触发方式 | 决策者 | 成本 | 适用场景 |
| --- | --- | --- | --- | --- |
| **直接参数面板** | 拖滑块 / 改数值 | 用户 | 零 token，秒级反馈 | 微调五官位置、壁厚等确定性参数 |
| **Agent 一键填充** | "根据我的设计生成 3D 参数" | LLM 依据分析特征 + 眼部 landmark 估算参数 → **用户确认** → 应用 | 一次 LLM 调用 | 从设计稿起步，冷启动 |
| **Agent 对话微调** | "眼睛再大一点，嘴角上扬" | LLM 产出参数 diff → 应用（生成类大改动仍走确认） | 一次 LLM 调用 | 自然语言描述改动 |

**结论：不需要"Agent 直接操控 Blender"这种通道。** Agent 的价值只在两处——把非结构化输入（设计分析、自然语言）翻译成参数，以及跨参数组合的估算；执行永远走确定性执行器。这样：

- 每一次改动都是一份可保存、可对比、可回滚的参数快照（与 2D 编辑器的 recipe 同构）；
- Agent 幻觉最多导致"参数夹取到合法范围"，不会产生破坏性命令；
- 直接操控不依赖任何 LLM，永远可用。

## 3. 执行通道（Blender 怎么被调起）

| 方案 | 原理 | 优点 | 缺点 |
| --- | --- | --- | --- |
| **A. 无头批处理（MVP）** | `blender -b -P build_shell.py -- --params params.json --out outdir` | 零常驻、可复用现有 job_store/queue 的任务-进度-取消-产物机制、崩溃隔离 | 每次冷启动 3–10s；无实时视口 |
| B. 常驻 RPC | Blender 内跑 socket add-on，后端长连接发命令 | 实时预览、增量改参数快 | 要维护 add-on 与会话生命周期、崩溃恢复 |

**MVP 选 A**，并把交互设计成"提交参数 → 后台任务 → 出预览图"（与现有生图 job 的体验一致）。方案 B 作为二期：先让参数流水线跑通，再谈实时。

**版本与形态约束**：

- 固定 **Blender 5.1**（几何节点输入走 5.1 的 `node_tree.interface` API；不兼容 3.x/4.x 的旧接口，脚本与文档均按 5.1 编写）。
- 建模全部用**纯 Python 脚本程序化构建**（骨架网格 + 几何节点 + 修改器），不依赖仓库里的二进制 `.blend` 模板——脚本和参数都可进 git，任何人 `blender -b -P` 即可复现。
- `BLENDER_PATH` 作为后端设置（默认 `blender`，走 PATH；本机安装可用路径覆盖）。

## 4. 参数注册表（Schema）

单一事实源：`blender/params/schema.json`。UI 面板和 Agent 工具的 JSON Schema 都从它生成。每个条目：

```json
{
  "key": "eye.hole_width_mm",
  "group": "eyes",
  "type": "float",
  "min": 30, "max": 90,
  "default": 66,
  "unit": "mm",
  "step": 0.5,
  "description": "单眼开孔宽（椭圆）",
  "sources": ["landmark", "design", "manual"],
  "affects": ["geometry"]
}
```

`sources` 标注该参数的推荐取值来源：`landmark`（从编辑器眼部标注直接换算）、`design`（由分析特征/设计图估算，Agent 负责）、`manual`（用户手调）。`affects` 区分改几何还是只改材质/贴图（影响是否需要重建几何，纯材质改动预览更快）。

### 4.1 默认尺寸推导（按实测身体数据）

输入（2026-10-09 实测）：**身高 1900mm、头围 640mm、肩宽 450mm；裸头宽实测 180mm、头高（下巴到头顶）实测 235mm**。穿戴约束：假发在壳体外每侧厚 2–3cm（取 25mm）；壳内左右海绵垫 2–4cm（取 30mm/侧，特指左右）；顶部垫层偏厚（取 35mm）；壳底沿离下巴约 1cm（10mm）。

| 量 | 推导 | 结果 |
| --- | --- | --- |
| 裸头深 | 头围 640 与实测头宽 180 反推（椭圆周长拟合） | ≈ 226mm |
| 壳外宽 | 180 + 左右海绵 30×2 + 壁厚 2.5×2 | **245mm** |
| 壳外高 | 头高 235 + 顶部垫层 35 + 壁厚 2.5 + 底沿延伸 10 | **283mm** |
| 壳外深 | 226 + 后脑海绵 25 + 壁厚 2.5 + 面部固有空间 8 | **262mm** |
| 眼中心离壳底沿 | 底沿间隙 10 + 头高/2 117.5 | **128mm** |
| 瞳距 | 真人 ≈63 按 animegao 比例放大 | **88mm** |
| 肩宽校验 | (壳外宽 245 + 假发 25×2) / 肩宽 450 = 295/450 | **≈ 1:1.53** ✓ 标准 animegao 大头比例 |

肩宽不合适的调节旋钮（按优先级）：减发量 / 换薄假发（每侧 20mm 时 1:1.60）/ 左右海绵取 20mm 下限；**壳外宽不能压破内腔下限**（裸头 180 + 左右海绵 40）。

### 4.2 参数清单（v1，默认值即上表推导结果）

长度单位一律毫米（真实打印尺度），角度用度。分组即 UI 面板的折叠分组。

**A. 头壳基础形（geometry）**

| key | 类型/范围 | 默认 | 说明 | 来源 |
| --- | --- | --- | --- | --- |
| `base.face_width_mm` | float 210–280 | 245 | 脸部最宽处（颧骨间距，外壳面） | fit |
| `base.face_height_mm` | float 250–320 | 283 | 壳底沿到壳外顶 | fit |
| `base.head_depth_mm` | float 230–300 | 262 | 前后深度（外壳面） | fit |
| `base.profile_forehead` | float 0–1 | 0.5 | 额头饱满度（截面控制点） | design |
| `base.profile_cheek` | float 0–1 | 0.5 | 脸颊肉感 | design |
| `base.profile_jaw` | float 0–1 | 0.5 | 下颌宽度收束 | design |
| `base.profile_chin` | float 0–1 | 0.5 | 下巴长度/尖圆 | design |
| `base.relief_depth_mm` | float 0–8 | 3 | 五官浮雕最大深度（animegao 要浅） | design |
| `base.cross_section_roundness` | float 0–1 | 0.6 | 横截面由扁到圆 | manual |
| `base.chin_gap_mm` | float 5–20 | 10 | 壳底沿到下巴尖的距离 | fit |

**B. 眼部（geometry）**

| key | 类型/范围 | 默认 | 说明 | 来源 |
| --- | --- | --- | --- | --- |
| `eye.hole_width_mm` | float 30–90 | 66 | 单眼开孔宽（椭圆） | landmark |
| `eye.hole_height_mm` | float 25–80 | 58 | 单眼开孔高 | landmark |
| `eye.interpupillary_mm` | float 60–130 | 88 | 瞳距 | landmark |
| `eye.center_height_mm` | float 80–170 | 128 | 眼中心离壳底沿的距离 | landmark |
| `eye.tilt_deg` | float −15–15 | 0 | 眼轴倾角（吊梢/垂眼） | landmark |
| `eye.socket_depth_mm` | float 0–15 | 8 | 眼窝凹进深度 | manual |
| `eye.socket_taper` | float 0–1 | 0.4 | 孔沿向内收的锥度 | manual |
| `eye.dome_bulge_mm` | float 0–12 | 0 | >0 时生成玻璃镜片凸面几何（否则只开孔） | manual |

> 眼部四个 `landmark` 来源参数由现有编辑器 landmark（leftEye/rightEye/chin，归一化坐标 × 基础形尺寸）直接换算，这是 2D 编辑器和 3D 参数之间最重要的自动桥。

**C. 鼻与嘴（geometry）**

| key | 类型/范围 | 默认 | 说明 | 来源 |
| --- | --- | --- | --- | --- |
| `nose.tip_height_mm` | float 0–6 | 1.5 | 鼻尖凸起（animegao 只有极小鼻尖） | design |
| `nose.width_mm` | float 4–15 | 7 | 鼻尖宽度 | design |
| `mouth.width_mm` | float 20–60 | 34 | 嘴线宽度 | design |
| `mouth.carve_depth_mm` | float 0–1.5 | 0 | 嘴线刻槽深（0 = 纯贴图上色，与提示词"嘴是平面彩绘"一致） | design |
| `mouth.smile_curve_deg` | float −20–20 | 5 | 嘴角上扬角度 | design |

**D. 耳朵（geometry，附加体）**

| key | 类型/范围 | 默认 | 说明 | 来源 |
| --- | --- | --- | --- | --- |
| `ears.style` | enum none/elf/animal/horn | none | 耳型（从分析特征映射） | design |
| `ears.position_z_mm` | float 0–60 | 25 | 附着点高度 | design |
| `ears.scale` | float 0.5–2 | 1 | 相对尺寸 | design |
| `ears.tilt_deg` | float −30–30 | 0 | 外撇角度 | design |
| `ears.thickness_mm` | float 2–6 | 3 | 耳片厚度 | manual |

**E. 内衬与结构（geometry）**

| key | 类型/范围 | 默认 | 说明 | 来源 |
| --- | --- | --- | --- | --- |
| `fit.pad_side_mm` | float 20–40 | 30 | 左右海绵垫单侧厚度（2–4cm） | fit |
| `fit.pad_top_mm` | float 20–50 | 35 | 顶部垫层厚度（偏厚） | fit |
| `fit.pad_back_mm` | float 15–40 | 25 | 后脑海绵垫厚度 | fit |
| `structure.wall_thickness_mm` | float 1.5–5 | 2.5 | 壳壁厚 | manual |
| `structure.neck_opening_mm` | float 120–220 | 180 | 底部开口直径（斜向穿入整个头） | manual |
| `structure.edge_band_mm` | float 0–20 | 8 | 壳沿收边带宽 | manual |
| `structure.split` | enum single/face+back | single | 是否分件（面壳+后壳） | manual |
| `structure.split_height_mm` | float 0–120 | 60 | 分件线高度（split 时生效） | manual |
| `structure.reg_pins` | int 0–8 | 4 | 分件定位销数量 | manual |
| `structure.drain_holes` | int 0–10 | 4 | 排水/树脂孔数量 | manual |
| `structure.remesh_mm` | float 0.5–4 | 1.5 | 重拓扑体素尺寸（文件大小/打印精度） | manual |

> 改 `fit.pad_*` / `base.chin_gap_mm` 会改变内腔与外形，属于几何重建；4.1 的推导仅用于生成默认值，运行时不做联动换算——面板上各参数独立可调。

**F. 外观与贴图（appearance，改材质不重建几何）**

| key | 类型/范围 | 默认 | 说明 | 来源 |
| --- | --- | --- | --- | --- |
| `appearance.base_map_source` | enum design/off | design | 是否把设计稿正视图投影为底色贴图 | design |
| `appearance.projection` | enum cylindrical/planar | cylindrical | 贴图投影方式 | manual |
| `appearance.roughness` | float 0.2–1 | 0.75 | 预览粗糙度（哑光壳面） | manual |
| `appearance.mouth_paint_opacity` | float 0–1 | 1 | 嘴线贴图不透明度（与 carve_depth 二选一） | design |

**G. 输出设置（job 级，不属于模型参数，但同一 schema 描述）**

`output.views`（默认 `front,three_quarter,side`）、`output.render_px`（默认 1280）、`output.export_formats`（默认 `glb,stl`）。

### 4.3 参数快照与版本

一次建模 = 一份**完整参数快照**（`{schema_version, params, source: "manual"|"agent", note}`）。快照与 2D 编辑器的 recipe 同构：存进项目、可回滚、可导出。Agent 产出的不是"改了什么命令"而是"新快照 = 旧快照 + diff"，diff 以便在聊天里展示"Agent 想改哪几个值"，用户确认后落快照。

## 5. 执行契约（Blender 脚本侧）

```
blender -b -P blender/build_shell.py -- \
  --params out/params.json \        # 4.3 的快照（含 schema_version）
  --design ref/design-front.png \   # 贴图/比例参考（可多张）
  --out out/                        # 产物目录
```

脚本职责（全部确定性，无随机、无 AI）：

1. 校验 `schema_version` 与参数范围（超界夹取并写入警告清单）。
2. 构建/更新几何：基础形（放样截面 → 壳体）→ 眼孔/鼻/嘴（布尔与浮雕）→ 耳朵附加体 → 内衬余量/壁厚/开口/分件/重拓扑。
3. 材质与贴图：设计稿圆柱投影、嘴线贴图、预览粗糙度。
4. 按输出设置渲染各视图 PNG，导出 GLB / STL。
5. 写 `out/result.json`：实际生效的参数（含夹取警告）、输出文件清单、耗时、Blender 版本——后端据此回报给 UI。

**目录布局**：

```
blender/
  build_shell.py            # CLI 入口（参数解析、校验、调度）
  shell_builder/            # 纯 bpy 模块：base_mesh.py / features.py / ears.py / materials.py / io.py
  params/schema.json        # 参数注册表（UI 与 Agent 工具的生成源）
  params/presets/*.json     # 预设（如「标准美少女壳」「圆脸壳」）
backend/app/blender/
  schema.py                 # 注册表的 pydantic 模型 + 校验/夹取（与 schema.json 同源生成）
  service.py                # 起子进程、解析 result.json、接 job_store
```

## 6. 与现有管线的集成点

- **任务**：新的 job 类型 `blender_build`，走现有 queue/job_store（进度：`构建几何 40% → 贴图 70% → 渲染 90%`），支持取消（杀子进程）。
- **产物**：渲染 PNG 走现有图片展示链路（进聊天/画布，`source: "blender"`，不加水印可选）；GLB/STL 进 `/api/generated` 旁的下载端点（`.stl/.glb` 要加进扩展名白名单）。
- **确认流**：Agent 填参/微调产出 diff → 复用现有"生成前确认"机制（`AGENT_MAX_GENERATIONS_PER_TURN` 同思路限频）。
- **参数快照**：作为 `DesignImage.source="blender"` 的兄弟概念挂在项目状态上（或独立 `state.blender_snapshots[]`，实施时定）；预览图可从快照"打开在 Blender 参数面板"再编辑。
- **前端**：参数面板复用 2D 编辑器的滑块架构（`schema.json` → 自动生成控件），放一个新 activity 项「3D」；预览渲染 + 下载按钮 + 快照历史。

## 7. 风险与不做的事

- **建模脚本的工作量是大头**：基础形放样和五官浮雕的观感需要多轮实测调校；先做「光头素壳 + 眼孔」的最小可看版本，再逐个加特征。
- **贴图投影变形**：圆柱投影在浅浮雕上可以接受，但设计稿风格与 3D 底色有风格差；MVP 只做底色投影，眼瞳精细贴图后置。
- **不做**：Blender GUI 内交互编辑（后端只起无头进程）、布料/假发模拟、骨骼绑定、渲染农场、AI 生成几何。
- **跨机部署**：Docker 镜像可选装 Blender（体积 +~300MB）；本地 Windows 走 `start-local.ps1` 检测 `BLENDER_PATH`，未装时 UI 隐藏 3D 入口并提示。

## 8. 实施阶段

1. **P0**：`schema.json` + pydantic 校验 + `build_shell.py` 最小素壳（基础形 + 眼孔 + 壁厚 + 单视图渲染 + GLB/STL）+ 后端 job 接入 + 前端参数面板（schema 自动生成滑块）与预览/下载。**此阶段无 Agent，纯直接操控。**
2. **P1**：landmark/分析特征 → 参数自动换算；Agent 工具 `propose_blender_params`（diff + 确认）；参数快照进项目版本。
3. **P2**：耳朵/嘴/分件/排孔等完整参数；预设库；Agent 对话微调。
4. **P3（可选）**：常驻 Blender 会话实时预览；3MF 多件导出。
