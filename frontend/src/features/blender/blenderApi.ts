import { z } from "zod";

const paramValueSchema = z.union([z.number(), z.string(), z.boolean(), z.array(z.string()), z.null()]);

export const paramSpecSchema = z.object({
  key: z.string(),
  group: z.string(),
  type: z.enum(["float", "int", "enum", "bool", "string", "multi"]),
  label: z.string(),
  label_en: z.string().default(""),
  description: z.string().default(""),
  default: paramValueSchema.optional(),
  min: z.number().optional(),
  max: z.number().optional(),
  step: z.number().optional(),
  unit: z.string().optional(),
  options: z.array(z.union([z.string(), z.number()])).optional(),
  nullable: z.boolean().optional(),
  readonly: z.boolean().optional(),
  max_length: z.number().optional(),
  scales_with_shell: z.boolean().optional(),
});

export const shellSchemaSchema = z.object({
  schema_version: z.number(),
  groups: z.array(z.object({ key: z.string(), label: z.string(), label_en: z.string().default("") })),
  params: z.array(paramSpecSchema),
});

export const presetSchema = z.object({
  id: z.string(),
  name: z.string(),
  params: z.record(z.string(), paramValueSchema),
});

// Mirrors the dict returned by blender/shell_builder/sizing.py:resolve().
export const derivedSchema = z.object({
  tier: z.string(),
  s_req: z.number(),
  s: z.number(),
  step: z.number().nullable().optional(),
  recommended_tier: z.string(),
  tightest_axis: z.string(),
  required_mm: z.array(z.number()),
  derived_from_wearer: z.array(z.number()).nullable().optional(),
  actual_mm: z.array(z.number()),
  meets: z.boolean(),
  view_spacing_mm: z.number(),
  view_spacing_source: z.string(),
  neck: z.object({ minor_mm: z.number(), major_mm: z.number() }),
  notes: z.array(z.string()).optional(),
  shoulder_ratio: z.number().optional(),
});

export const validatedSchema = z.object({
  schema_version: z.number(),
  params: z.record(z.string(), paramValueSchema),
  warnings: z.array(z.string()),
  derived: derivedSchema,
});

const resultFileSchema = z
  .object({
    kind: z.string(),
    name: z.string(),
    format: z.string(),
    view: z.string().optional(),
    part: z.string().optional(),
    bytes: z.number().optional(),
    manifold: z.boolean().optional(),
    size_mm: z.array(z.number()).optional(),
  })
  .passthrough();

export const blenderJobSchema = z.object({
  id: z.string(),
  status: z.string(),
  progress: z.number(),
  phase_label: z.string(),
  created_at: z.string(),
  finished_at: z.string().nullable(),
  params: z.record(z.string(), paramValueSchema),
  derived: derivedSchema.partial().passthrough(),
  warnings: z.array(z.string()),
  error: z.string().nullable(),
  note: z.string().default(""),
  source: z.string().default("manual"),
  log_tail: z.array(z.string()).default([]),
  result: z
    .object({
      files: z.array(resultFileSchema).default([]),
      measured: z.record(z.string(), z.number()).optional(),
      timings_s: z.record(z.string(), z.number()).optional(),
    })
    .passthrough()
    .nullable(),
});

export const blenderStatusSchema = z.object({
  available: z.boolean(),
  reasons: z.array(z.string()),
});

export type ParamValue = z.infer<typeof paramValueSchema>;
export type ParamSpec = z.infer<typeof paramSpecSchema>;
export type ShellSchema = z.infer<typeof shellSchemaSchema>;
export type Preset = z.infer<typeof presetSchema>;
export type Validated = z.infer<typeof validatedSchema>;
export type BlenderJob = z.infer<typeof blenderJobSchema>;
export type BlenderStatus = z.infer<typeof blenderStatusSchema>;
export type Params = Record<string, ParamValue>;

export const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

async function send(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`/api/blender${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // keep the status text
    }
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return response.json();
}

const jsonInit = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export async function getShellSchema(): Promise<{ schema: ShellSchema; presets: Preset[] }> {
  const body = z.object({ schema: shellSchemaSchema, presets: z.array(presetSchema) }).parse(await send("/schema"));
  return body;
}

export async function getBlenderStatus(): Promise<BlenderStatus> {
  return blenderStatusSchema.parse(await send("/status"));
}

export async function validateParams(params: Params, schemaVersion: number): Promise<Validated> {
  return validatedSchema.parse(await send("/validate", jsonInit({ schema_version: schemaVersion, params })));
}

export async function createBuild(params: Params, schemaVersion: number, note = ""): Promise<BlenderJob> {
  return blenderJobSchema.parse(await send("/jobs", jsonInit({ schema_version: schemaVersion, params, note, source: "manual" })));
}

export async function getBuild(jobId: string): Promise<BlenderJob> {
  return blenderJobSchema.parse(await send(`/jobs/${encodeURIComponent(jobId)}`));
}

export async function listBuilds(): Promise<BlenderJob[]> {
  return z.array(blenderJobSchema).parse(await send("/jobs"));
}

export async function cancelBuild(jobId: string): Promise<BlenderJob> {
  return blenderJobSchema.parse(await send(`/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }));
}

export function buildFileUrl(jobId: string, name: string): string {
  return `/api/blender/jobs/${encodeURIComponent(jobId)}/files/${encodeURIComponent(name)}`;
}

export function defaultParams(schema: ShellSchema): Params {
  const params: Params = {};
  for (const spec of schema.params) {
    if (spec.readonly) continue;
    const value = spec.default ?? null;
    params[spec.key] = Array.isArray(value) ? [...value] : value;
  }
  return params;
}
