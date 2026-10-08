import { z } from "zod";

export const backendSettingsSchema = z.object({
  writable: z.boolean(),
  values: z.record(z.string(), z.string()),
  // API keys are write-only: only whether they are set, where from, and the last characters.
  secrets: z.record(z.string(), z.object({ set: z.boolean(), source: z.string(), hint: z.string() })),
  overridden: z.array(z.string()),
  options: z.record(z.string(), z.array(z.string())),
  status: z.record(z.string(), z.boolean()),
});

export type BackendSettings = z.infer<typeof backendSettingsSchema>;

const commonRequirements: Record<string, string> = {
  claude_code: "claude_cli",
  codex: "codex_cli",
  codex_bridge: "codex_bridge",
  siliconflow: "siliconflow_key",
  ark: "ark_key",
};

/** Status flag a backend needs to be usable; backends without an entry (fixtures) are always usable. */
export function backendRequirement(settingKey: string, option: string): string | undefined {
  if (option === "openai_compatible") return settingKey === "llm_provider" ? "analysis_llm_key" : "agent_llm_key";
  return commonRequirements[option];
}

async function request(init?: RequestInit): Promise<BackendSettings> {
  const response = await fetch("/api/settings", init);
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
  return backendSettingsSchema.parse(await response.json());
}

export function getBackendSettings() {
  return request();
}

/** A null value drops the saved override, so the .env value applies again. */
export function updateBackendSettings(values: Record<string, string | null>) {
  return request({ method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values }) });
}

export function resetBackendSettings() {
  return request({ method: "DELETE" });
}
