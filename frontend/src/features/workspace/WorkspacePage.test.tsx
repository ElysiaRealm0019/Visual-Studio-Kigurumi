import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, waitFor } from "@testing-library/react";
import { forwardRef } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import { WorkspacePage } from "./WorkspacePage";

const conversation = {
  id: "p1",
  title: "猫娘头壳",
  created_at: "2026-10-09T00:00:00Z",
  locale: "zh-CN",
  status: "idle",
  running: false,
  last_seq: 1,
  events: [],
  state: {
    references: [],
    images: [] as Array<ReturnType<typeof version>>,
    current_design_id: null as string | null,
    approved_design_id: null as string | null,
    current_front_id: null as string | null,
    approved_front_id: null as string | null,
  },
};

function version(id: string, role: string, source: string) {
  return {
    id,
    job_id: "job-1",
    instructions: "",
    parent_id: null as string | null,
    role,
    source,
    url: `/api/generated/${id}.png`,
    width: 800,
    height: 1100,
  };
}

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const refresh = vi.fn(async () => undefined);
const saveManualVersion = vi.fn();
const correctEyeSymmetry = vi.fn();

vi.mock("../agent/useConversation", () => ({
  // A fresh conversation object per render, the way the real hook hands over a new fetch result.
  useConversation: () => ({
    conversation: { ...conversation, state: { ...conversation.state, images: [...conversation.state.images] } },
    events: [],
    loadError: null,
    refresh,
    running: false,
    setRunning: vi.fn(),
  }),
}));

vi.mock("../agent/agentApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../agent/agentApi")>()),
  saveManualVersion: (...args: unknown[]) => saveManualVersion(...args),
}));

vi.mock("./eyeSymmetryCorrection", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./eyeSymmetryCorrection")>()),
  correctEyeSymmetry: (...args: unknown[]) => correctEyeSymmetry(...args),
}));

vi.mock("./useEditorDrafts", () => ({
  useEditorDrafts: () => ({ draftIds: new Set<string>(), discard: vi.fn(), loaded: null, schedule: vi.fn() }),
}));

vi.mock("../editor/EditorWorkspace", () => ({
  EditorWorkspace: forwardRef(() => null),
  recipeHasEdits: () => false,
}));

vi.mock("./AgentPanel", () => ({ AgentPanel: () => null }));

vi.mock("./ExplorerPanel", () => ({ ExplorerPanel: () => null }));

/** The workspace tree, re-created on purpose: each rerender must see the conversation's current images. */
function page() {
  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/p/p1"]}>
        <Routes>
          <Route element={<WorkspacePage />} path="/p/:projectId" />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("WorkspacePage automatic eye symmetry", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("zh-CN");
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    conversation.state.images = [];
    conversation.state.current_design_id = null;
    conversation.state.current_front_id = null;
  });

  it("corrects a freshly generated uneven front view and saves it as its own version", async () => {
    conversation.state.images = [version("design-1", "design", "agent")];
    conversation.state.current_design_id = "design-1";
    const corrected = new Blob(["corrected"], { type: "image/png" });
    correctEyeSymmetry.mockResolvedValue({
      blob: corrected,
      height: 1100,
      plan: { axisX: 0.5, eyeY: 0.43, halfSpacing: 0.08 },
      width: 800,
    });
    saveManualVersion.mockImplementation(async () => {
      conversation.state.images = [...conversation.state.images, version("front-1", "front", "auto_symmetry")];
      return conversation;
    });

    const { rerender } = render(page());
    // A version that already existed when the project opened is never re-corrected.
    expect(correctEyeSymmetry).not.toHaveBeenCalled();

    conversation.state.images = [...conversation.state.images, version("front-1", "front", "agent")];
    rerender(page());

    await waitFor(() => expect(saveManualVersion).toHaveBeenCalledTimes(1));
    expect(correctEyeSymmetry).toHaveBeenCalledWith({
      imageUrl: "/api/agent/conversations/p1/versions/front-1/source",
      role: "front",
    });
    expect(saveManualVersion).toHaveBeenCalledWith("p1", {
      baseImageId: "front-1",
      image: corrected,
      note: "自动修正：左右眼原本不平，已对齐到同一水平线并镜像成对称的一对。",
      recipe: {},
      source: "auto_symmetry",
    });
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });

  it("leaves a version whose eyes are already symmetric alone", async () => {
    conversation.state.images = [version("design-1", "design", "agent")];
    conversation.state.current_design_id = "design-1";
    correctEyeSymmetry.mockResolvedValue(null);

    const { rerender } = render(page());
    conversation.state.images = [...conversation.state.images, version("front-1", "front", "agent")];
    rerender(page());

    await waitFor(() => expect(correctEyeSymmetry).toHaveBeenCalledTimes(1));
    expect(saveManualVersion).not.toHaveBeenCalled();
  });

  it("skips the user's own manual save but still corrects the next generation", async () => {
    conversation.state.images = [version("front-1", "front", "agent")];
    correctEyeSymmetry.mockResolvedValue(null);

    const { rerender } = render(page());
    conversation.state.images = [...conversation.state.images, version("front-2", "front", "manual")];
    rerender(page());

    // Give the pass time to run before asserting it ignored the manual version.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(correctEyeSymmetry).not.toHaveBeenCalled();

    conversation.state.images = [...conversation.state.images, version("front-3", "front", "agent")];
    rerender(page());

    // Exactly one correction, and for the generated version rather than the manual one.
    await waitFor(() => expect(correctEyeSymmetry).toHaveBeenCalledTimes(1));
    expect(correctEyeSymmetry).toHaveBeenCalledWith({
      imageUrl: "/api/agent/conversations/p1/versions/front-3/source",
      role: "front",
    });
  });
});
