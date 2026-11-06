import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import { BlenderPage } from "./BlenderPage";

const schema = {
  schema_version: 1,
  groups: [
    { key: "size", label: "尺寸档位", label_en: "Size" },
    { key: "eyes", label: "眼孔", label_en: "Eye holes" },
  ],
  params: [
    { key: "base.size_tier", group: "size", type: "enum", options: ["lite", "standard", "extra", "custom"], default: "standard", label: "尺寸档位", label_en: "Size tier" },
    { key: "base.face_width_mm", group: "size", type: "float", min: 180, max: 320, step: 0.5, default: null, nullable: true, unit: "mm", label: "宽度需求下限", label_en: "Min. width" },
    { key: "eye.width_scale", group: "eyes", type: "float", min: 0.6, max: 1.6, step: 0.01, default: 1, label: "眼孔宽倍率", label_en: "Eye width ×" },
    { key: "structure.neck_opening_mm", group: "size", type: "float", readonly: true, default: null, label: "颈口短轴", label_en: "Neck" },
  ],
};

function derived(s: number) {
  return {
    tier: s === 1 ? "standard" : "extra", s_req: 1, s, recommended_tier: "standard", tightest_axis: "width",
    required_mm: [236.9, 243.3, 284.7], actual_mm: [236.9 * s, 243.3 * s, 284.7 * s], meets: true,
    view_spacing_mm: 68.3, view_spacing_source: "default", neck: { minor_mm: 130.1 * s, major_mm: 132.6 * s }, notes: [],
  };
}

const finishedJob = {
  id: "job-1", status: "succeeded", progress: 100, phase_label: "构建完成", created_at: "2026-10-14T00:00:00Z",
  finished_at: "2026-10-14T00:00:20Z", params: { "base.size_tier": "extra" }, derived: derived(1.09), warnings: [], error: null,
  note: "", source: "manual",
  result: {
    files: [
      { kind: "render", view: "front", format: "png", name: "render_front.png" },
      { kind: "model", part: "front_shell", format: "stl", name: "front_shell.stl", bytes: 2_097_152, manifold: false },
    ],
    measured: { width_mm: 258.2, depth_mm: 265.2, height_mm: 310.3 },
  },
};

function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <BlenderPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockApi({ available = true } = {}) {
  const calls: { url: string; body?: unknown }[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, body });
    if (url === "/api/blender/schema") return new Response(JSON.stringify({ schema, presets: [{ id: "extra", name: "Extra", params: { "base.size_tier": "extra" } }] }));
    if (url === "/api/blender/status") return new Response(JSON.stringify({ available, reasons: available ? [] : ["未设置 BLENDER_SHELL_BLEND"] }));
    if (url === "/api/blender/jobs" && init?.method === "POST") return new Response(JSON.stringify(finishedJob), { status: 201 });
    if (url === "/api/blender/jobs") return new Response(JSON.stringify([]));
    if (url === "/api/blender/jobs/job-1") return new Response(JSON.stringify(finishedJob));
    if (url === "/api/blender/validate") {
      const params = (body as { params: Record<string, unknown> }).params;
      const s = params["base.size_tier"] === "extra" ? 1.09 : 1;
      return new Response(JSON.stringify({ schema_version: 1, params, warnings: [], derived: derived(s) }));
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

describe("BlenderPage", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("zh-CN");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("builds controls from the schema and shows the derived size", async () => {
    const calls = mockApi();
    renderPage();

    expect(await screen.findByLabelText("眼孔宽倍率")).toHaveValue(1);
    expect(screen.queryByText("颈口短轴")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("blender-summary")).toHaveTextContent("1.00"));

    fireEvent.change(screen.getByLabelText("尺寸档位"), { target: { value: "extra" } });
    await waitFor(() => expect(screen.getByTestId("blender-summary")).toHaveTextContent("1.09"));
    const last = calls.filter((call) => call.url === "/api/blender/validate").at(-1);
    expect((last?.body as { params: Record<string, unknown> }).params["base.size_tier"]).toBe("extra");
    expect((last?.body as { params: Record<string, unknown> }).params["base.face_width_mm"]).toBeNull();
  });

  it("submits a build and lists renders and downloads", async () => {
    const calls = mockApi();
    renderPage();
    const button = await screen.findByRole("button", { name: /构建/ });
    await waitFor(() => expect(button).toBeEnabled());

    fireEvent.click(button);

    const jobView = await screen.findByTestId("blender-job");
    expect(jobView).toHaveTextContent("258.2 × 265.2 × 310.3");
    expect(screen.getByAltText("front")).toHaveAttribute("src", "/api/blender/jobs/job-1/files/render_front.png");
    expect(screen.getByRole("link", { name: /前壳 · STL/ })).toHaveAttribute("href", "/api/blender/jobs/job-1/files/front_shell.stl");
    expect(jobView).toHaveTextContent("非流形");
    expect(calls.some((call) => call.url === "/api/blender/jobs" && call.body !== undefined)).toBe(true);
  });

  it("disables building when Blender is not configured", async () => {
    mockApi({ available: false });
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("BLENDER_SHELL_BLEND");
    expect(await screen.findByRole("button", { name: /构建/ })).toBeDisabled();
  });
});
