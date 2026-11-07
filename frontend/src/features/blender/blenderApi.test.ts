import { afterEach, describe, expect, it, vi } from "vitest";
import { blenderJobSchema, getBuild, listBuilds } from "./blenderApi";

function derived(s: number) {
  return {
    tier: "standard", s_req: 1, s, recommended_tier: "standard", tightest_axis: "width",
    required_mm: [236.9, 243.3, 284.7], actual_mm: [236.9 * s, 243.3 * s, 284.7 * s], meets: true,
    view_spacing_mm: 68.3, view_spacing_source: "default", neck: { minor_mm: 130.1 * s, major_mm: 132.6 * s },
  };
}

// The backend omits `result` until the build produces one.
const jobWithoutResult = {
  id: "job-1", status: "queued", progress: 0, phase_label: "排队中",
  created_at: "2026-10-14T00:00:00Z", finished_at: null,
  params: { "base.size_tier": "extra" }, derived: derived(1.09), warnings: [], error: null,
};

const jobWithResult = {
  ...jobWithoutResult,
  status: "succeeded",
  result: {
    files: [{ kind: "render", view: "front", format: "png", name: "render_front.png" }],
    measured: { width_mm: 258.2, depth_mm: 265.2, height_mm: 310.3 },
  },
};

describe("blenderJobSchema", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses a job payload that omits result", () => {
    const parsed = blenderJobSchema.parse(jobWithoutResult);
    expect(parsed.result).toBeUndefined();
    expect(parsed.note).toBe("");
    expect(parsed.log_tail).toEqual([]);
  });

  it("still accepts a null or populated result", () => {
    expect(blenderJobSchema.parse({ ...jobWithoutResult, result: null }).result).toBeNull();
    expect(blenderJobSchema.parse(jobWithResult).result?.files).toHaveLength(1);
  });

  it("parses list and detail responses whose jobs omit result", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/blender/jobs") return new Response(JSON.stringify([jobWithoutResult]));
        if (url === "/api/blender/jobs/job-1") return new Response(JSON.stringify(jobWithoutResult));
        return new Response("{}", { status: 404 });
      }),
    );

    const [listed] = await listBuilds();
    const detail = await getBuild("job-1");
    expect(listed.result).toBeUndefined();
    expect(detail.result).toBeUndefined();
  });
});
