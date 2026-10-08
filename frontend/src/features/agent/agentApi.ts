import { z } from "zod";

const API_BASE = "/api/agent";

export const agentEventSchema = z
  .object({
    seq: z.number(),
    type: z.string(),
    created_at: z.string().optional(),
  })
  .passthrough();

export type AgentEvent = z.infer<typeof agentEventSchema> & Record<string, unknown>;

const referenceSchema = z.object({
  id: z.string(),
  key: z.string(),
  kind: z.string(),
  url: z.string(),
  file_name: z.string(),
  note: z.string().default(""),
});

const designImageSchema = z.object({
  id: z.string(),
  role: z.enum(["design", "design_turnaround", "front", "turnaround"]),
  job_id: z.string(),
  url: z.string(),
  width: z.number(),
  height: z.number(),
  instructions: z.string().default(""),
  parent_id: z.string().nullable().default(null),
  source: z.enum(["agent", "manual", "local_revision"]).default("agent"),
});

export type ApproveAction = "approve_design" | "approve_front";

/** Stage 1 roles are 2D character designs; stage 2 roles are the physical head shell. */
export function isDesignRole(role: string | undefined): boolean {
  return role === "design" || role === "design_turnaround";
}

export function isTurnaroundRole(role: string | undefined): boolean {
  return role === "turnaround" || role === "design_turnaround";
}

export function approveActionFor(role: string | undefined): ApproveAction | null {
  if (isDesignRole(role)) return "approve_design";
  return role === "front" ? "approve_front" : null;
}

export const conversationSchema = z.object({
  id: z.string(),
  title: z.string(),
  created_at: z.string(),
  locale: z.string(),
  status: z.string(),
  running: z.boolean(),
  last_seq: z.number(),
  events: z.array(agentEventSchema),
  state: z.object({
    references: z.array(referenceSchema),
    images: z.array(designImageSchema),
    current_design_id: z.string().nullable().default(null),
    approved_design_id: z.string().nullable().default(null),
    current_front_id: z.string().nullable(),
    approved_front_id: z.string().nullable(),
  }),
});

export const conversationSummarySchema = z.object({
  id: z.string(),
  title: z.string(),
  created_at: z.string(),
  status: z.string(),
  thumbnail_url: z.string().nullable().default(null),
  version_count: z.number().default(0),
});

export type Conversation = z.infer<typeof conversationSchema>;
export type ConversationSummary = z.infer<typeof conversationSummarySchema>;
export type DesignImage = z.infer<typeof designImageSchema>;

export class AgentApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<S extends z.ZodTypeAny>(path: string, schema: S, init?: RequestInit): Promise<z.output<S>> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // keep statusText
    }
    throw new AgentApiError(detail, response.status);
  }
  return schema.parse(await response.json());
}

export function listConversations() {
  return request("/conversations", z.array(conversationSummarySchema));
}

export function createConversation(locale: string, title = "") {
  return request("/conversations", conversationSchema, {
    body: JSON.stringify({ locale, title }),
    headers: { "Content-Type": "application/json" },
    method: "POST",
  });
}

export function getConversation(id: string) {
  return request(`/conversations/${id}`, conversationSchema);
}

export function renameConversation(id: string, title: string) {
  return request(`/conversations/${id}`, conversationSchema, {
    body: JSON.stringify({ title }),
    headers: { "Content-Type": "application/json" },
    method: "PATCH",
  });
}

export function uploadReferences(id: string, files: File[]) {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  return request(`/conversations/${id}/references`, conversationSchema, { body: form, method: "POST" });
}

/** Unwatermarked pixels of a version: the editor works on these so watermarks never stack. */
export function versionSourceUrl(id: string, imageId: string) {
  return `${API_BASE}/conversations/${id}/versions/${imageId}/source`;
}

export function saveManualVersion(
  id: string,
  { baseImageId, image, note, recipe }: { baseImageId: string; image: Blob; note: string; recipe: unknown },
) {
  const form = new FormData();
  form.append("base_image_id", baseImageId);
  form.append("note", note);
  form.append("recipe", JSON.stringify(recipe));
  form.append("image", image, "edit.png");
  return request(`/conversations/${id}/versions`, conversationSchema, { body: form, method: "POST" });
}

export function startLocalRevision(
  id: string,
  payload: {
    baseImageId: string;
    baseImage: Blob;
    mask: Blob;
    editNote: string;
    lockOutside: boolean;
    referenceIds: string[];
    referenceFile?: File;
  },
) {
  const form = new FormData();
  form.append("base_image_id", payload.baseImageId);
  form.append("edit_note", payload.editNote);
  form.append("lock_outside", String(payload.lockOutside));
  form.append("reference_ids", JSON.stringify(payload.referenceIds));
  form.append("mask", payload.mask, "mask.png");
  form.append("base_image", payload.baseImage, "base.png");
  if (payload.referenceFile) form.append("reference_file", payload.referenceFile, payload.referenceFile.name);
  return request(`/conversations/${id}/local-revision`, conversationSchema, { body: form, method: "POST" });
}

export function startAnnotatedRevision(id: string, payload: { baseImageId: string; image: Blob; instructions: string }) {
  const form = new FormData();
  form.append("base_image_id", payload.baseImageId);
  form.append("instructions", payload.instructions);
  form.append("image", payload.image, "annotated.png");
  return request(`/conversations/${id}/annotated-revision`, conversationSchema, { body: form, method: "POST" });
}

export function deleteConversation(id: string) {
  return request(`/conversations/${id}`, z.object({ deleted: z.boolean() }), { method: "DELETE" });
}

export function cancelRun(id: string) {
  return request(`/conversations/${id}/cancel`, z.object({ cancelling: z.boolean() }), { method: "POST" });
}

export function sendMessage(
  id: string,
  { text = "", files = [], action, imageId }: { text?: string; files?: File[]; action?: ApproveAction; imageId?: string },
) {
  const form = new FormData();
  form.append("text", text);
  if (action) form.append("action", action);
  if (imageId) form.append("image_id", imageId);
  for (const file of files) form.append("files", file, file.name);
  return request(`/conversations/${id}/messages`, conversationSchema, { body: form, method: "POST" });
}

/** Subscribe to live agent events after `afterSeq`. Returns an unsubscribe function. */
export function subscribeToEvents(id: string, afterSeq: number, onEvent: (event: AgentEvent) => void) {
  const source = new EventSource(`${API_BASE}/conversations/${id}/events?after=${afterSeq}`);
  source.addEventListener("agent", (message) => {
    const parsed = agentEventSchema.safeParse(JSON.parse((message as MessageEvent<string>).data));
    if (parsed.success) onEvent(parsed.data as AgentEvent);
  });
  return () => source.close();
}

/** Apply a new event to the event list: patches update their target in place, others append. */
export function applyEvent(events: AgentEvent[], event: AgentEvent): AgentEvent[] {
  if (events.some((existing) => existing.seq === event.seq)) return events;
  if (event.type === "event_patch") {
    const target = event.target_seq as number;
    const patch = (event.patch ?? {}) as Record<string, unknown>;
    return events.map((existing) => (existing.seq === target ? { ...existing, ...patch } : existing));
  }
  return [...events, event];
}

/** Downscale large uploads client-side; generation backends cap inputs at ~2048px anyway. */
export async function prepareUpload(file: File, maxSide = 2048): Promise<File> {
  if (!file.type.startsWith("image/")) return file;
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    return file;
  }
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
  if (scale >= 1) {
    bitmap.close();
    return file;
  }
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d")?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
  if (!blob) return file;
  return new File([blob], file.name.replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg" });
}
