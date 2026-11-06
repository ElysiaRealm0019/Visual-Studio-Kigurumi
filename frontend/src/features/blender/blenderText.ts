import type { ParamSpec } from "./blenderApi";

/** Strings for the 3D page. Parameter names come from blender/params/schema.json (label / label_en). */
const zh = {
  title: "3D 头壳",
  open: "3D 头壳",
  intro: "调整参数后构建：Blender 在后台生成预览图和可打印模型（前壳 / 后壳 / 眼线件）。尺寸按档位或需求下限等比缩放，五官随之缩放，视孔间距与工艺量为真实毫米。",
  unavailable: "3D 构建不可用：",
  loadFailed: "无法读取参数表",
  preset: "预设",
  presetPick: "选择预设…",
  reset: "恢复默认",
  auto: "自动",
  build: "构建",
  building: "构建中…",
  cancel: "取消",
  summary: "尺寸",
  scale: "缩放系数 s",
  required: "需求下限（宽 / 深 / 高）",
  actual: "实际外壳（宽 / 深 / 高）",
  recommended: "推荐档位",
  tightest: "最紧方向",
  neck: "颈口（短轴 × 长轴）",
  viewSpacing: "视孔间距",
  shoulder: "肩宽比",
  warnings: "提示",
  renders: "预览",
  downloads: "下载",
  history: "构建记录",
  noHistory: "还没有构建",
  loadParams: "载入参数",
  measured: "实测",
  nonManifold: "非流形",
  failed: "构建失败",
  cancelled: "已取消",
  axis: { width: "宽", depth: "深", height: "高" } as Record<string, string>,
  spacingSource: { manual: "手动", ipd: "取瞳距", default: "默认" } as Record<string, string>,
  parts: { front_shell: "前壳", back_shell: "后壳", eyeline: "眼线件" } as Record<string, string>,
};

type Text = typeof zh;

const en: Text = {
  title: "3D head shell",
  open: "3D head shell",
  intro: "Adjust the parameters and build: Blender renders previews and exports printable parts (front shell / back shell / eyeline) in the background. Size scales uniformly by tier or minimum fit; features scale with it, while view-hole spacing and process values stay in real millimetres.",
  unavailable: "3D builds are unavailable: ",
  loadFailed: "Could not load the parameter registry",
  preset: "Preset",
  presetPick: "Choose a preset…",
  reset: "Reset",
  auto: "Auto",
  build: "Build",
  building: "Building…",
  cancel: "Cancel",
  summary: "Size",
  scale: "Scale s",
  required: "Minimum (W / D / H)",
  actual: "Actual shell (W / D / H)",
  recommended: "Recommended tier",
  tightest: "Tightest axis",
  neck: "Neck (minor × major)",
  viewSpacing: "View-hole spacing",
  shoulder: "Shoulder ratio",
  warnings: "Notes",
  renders: "Preview",
  downloads: "Downloads",
  history: "Builds",
  noHistory: "No builds yet",
  loadParams: "Load parameters",
  measured: "Measured",
  nonManifold: "non-manifold",
  failed: "Build failed",
  cancelled: "Cancelled",
  axis: { width: "width", depth: "depth", height: "height" },
  spacingSource: { manual: "manual", ipd: "from IPD", default: "default" },
  parts: { front_shell: "Front shell", back_shell: "Back shell", eyeline: "Eyeline" },
};

const ja: Text = {
  ...en,
  title: "3D ヘッドシェル",
  open: "3D ヘッドシェル",
  build: "ビルド",
  building: "ビルド中…",
  cancel: "キャンセル",
  reset: "初期値に戻す",
  preset: "プリセット",
  renders: "プレビュー",
  downloads: "ダウンロード",
  history: "ビルド履歴",
};

export function blenderText(language: string | undefined): Text {
  if (language?.startsWith("en")) return en;
  if (language?.startsWith("ja")) return ja;
  return zh;
}

export function specLabel(spec: { label: string; label_en: string }, language: string | undefined): string {
  return language?.startsWith("zh") || !spec.label_en ? spec.label : spec.label_en;
}

export function formatValue(spec: ParamSpec, value: unknown): string {
  if (typeof value !== "number") return String(value ?? "");
  const decimals = spec.step !== undefined && spec.step < 1 ? Math.min(3, String(spec.step).split(".")[1]?.length ?? 2) : 0;
  return value.toFixed(spec.type === "int" ? 0 : decimals);
}
