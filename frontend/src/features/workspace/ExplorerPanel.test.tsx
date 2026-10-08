import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import type { Conversation } from "../agent/agentApi";
import { ExplorerPanel } from "./ExplorerPanel";

afterEach(cleanup);

beforeAll(async () => {
  await i18n.changeLanguage("zh-CN");
});

function image(id: string, role: "front" | "turnaround", parent: string | null, source: "agent" | "manual" | "local_revision") {
  return { id, role, job_id: "", url: `/api/generated/${id}.webp`, width: 800, height: 1100, instructions: "", parent_id: parent, source };
}

const conversation: Conversation = {
  id: "p",
  title: "demo",
  created_at: "",
  locale: "zh-CN",
  status: "idle",
  running: false,
  last_seq: 0,
  events: [],
  state: {
    references: [{ id: "ref-1", key: "k", kind: "front", url: "/r.png", file_name: "r.png", note: "" }],
    images: [
      image("front-1", "front", null, "agent"),
      image("front-2", "front", "front-1", "manual"),
      image("front-3", "front", "front-2", "local_revision"),
      image("turnaround-1", "turnaround", "front-2", "agent"),
    ],
    current_front_id: "front-3",
    approved_front_id: "front-2",
  },
};

describe("ExplorerPanel", () => {
  it("nests versions under the version they were derived from and labels their source", () => {
    const onOpenVersion = vi.fn();
    render(
      <ExplorerPanel
        activeVersionId="front-2"
        conversation={conversation}
        disabled={false}
        onAddReferences={vi.fn()}
        onOpenImage={vi.fn()}
        onOpenVersion={onOpenVersion}
      />,
    );
    const front2 = screen.getByText("front-2").closest("li") as HTMLElement;
    expect(within(front2).getByText("front-3")).toBeTruthy();
    expect(within(front2).getByText("turnaround-1")).toBeTruthy();
    expect(within(front2).getByText("手动")).toBeTruthy();
    expect(within(front2).getAllByText("已确认")).toHaveLength(1);
    expect(screen.getByText("front-2").closest("button")).toHaveAttribute("data-active", "true");

    fireEvent.click(screen.getByText("turnaround-1"));
    expect(onOpenVersion).toHaveBeenCalledWith("turnaround-1");
  });

  it("uploads references from the explorer", () => {
    const onAddReferences = vi.fn();
    const { container } = render(
      <ExplorerPanel
        activeVersionId={null}
        conversation={conversation}
        disabled={false}
        onAddReferences={onAddReferences}
        onOpenImage={vi.fn()}
        onOpenVersion={vi.fn()}
      />,
    );
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["x"], "a.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(onAddReferences).toHaveBeenCalledWith([file]);
  });
});
