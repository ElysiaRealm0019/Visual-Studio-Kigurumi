import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import { applyEvent, type AgentEvent, type Conversation } from "./agentApi";
import { ChatTimeline } from "./ChatTimeline";

function conversation(approved: string | null = null): Conversation {
  return {
    id: "c",
    title: "",
    created_at: "",
    locale: "zh-CN",
    status: "idle",
    running: false,
    last_seq: 0,
    events: [],
    state: { references: [], images: [], current_front_id: "front-1", approved_front_id: approved },
  };
}

const frontImage: AgentEvent = {
  seq: 3,
  type: "image",
  role: "front",
  image_id: "front-1",
  url: "/api/generated/a.webp",
  width: 800,
  height: 1100,
};

afterEach(cleanup);

beforeAll(async () => {
  await i18n.changeLanguage("zh-CN");
});

describe("applyEvent", () => {
  it("appends new events, ignores duplicates and applies patches in place", () => {
    const tool: AgentEvent = { seq: 1, type: "tool", tool: "generate_front_view", status: "running", progress: 0 };
    let events = applyEvent([], tool);
    events = applyEvent(events, tool);
    expect(events).toHaveLength(1);
    events = applyEvent(events, { seq: 2, type: "event_patch", target_seq: 1, patch: { status: "succeeded", progress: 100 } });
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ status: "succeeded", progress: 100 });
  });
});

describe("ChatTimeline", () => {
  it("offers approval on the latest front view and sends it", () => {
    const onApprove = vi.fn();
    render(
      <ChatTimeline conversation={conversation()} events={[frontImage]} onApprove={onApprove} onOpenImage={vi.fn()} running={false} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /就用这张/ }));
    expect(onApprove).toHaveBeenCalledWith("front-1");
  });

  it("hides approval while running and shows the approved badge afterwards", () => {
    const { rerender } = render(
      <ChatTimeline conversation={conversation()} events={[frontImage]} onApprove={vi.fn()} onOpenImage={vi.fn()} running />,
    );
    expect(screen.queryByRole("button", { name: /就用这张/ })).toBeNull();
    rerender(
      <ChatTimeline
        conversation={conversation("front-1")}
        events={[frontImage]}
        onApprove={vi.fn()}
        onOpenImage={vi.fn()}
        running={false}
      />,
    );
    expect(screen.queryByRole("button", { name: /就用这张/ })).toBeNull();
    expect(screen.getByText("已确认")).toBeTruthy();
  });

  it("renders tool progress and failures", () => {
    render(
      <ChatTimeline
        conversation={conversation()}
        events={[
          { seq: 1, type: "tool", tool: "generate_turnaround", status: "failed", error: "No approved front view." },
        ]}
        onApprove={vi.fn()}
        onOpenImage={vi.fn()}
        running={false}
      />,
    );
    expect(screen.getByText("生成头壳四视图")).toBeTruthy();
    expect(screen.getByText("No approved front view.")).toBeTruthy();
  });
});
