import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import type { AgentEvent, Conversation, DesignImage } from "../agent/agentApi";
import { ChatTimeline } from "../agent/ChatTimeline";
import { projectStages } from "./WorkspacePage";

afterEach(cleanup);

beforeAll(async () => {
  await i18n.changeLanguage("zh-CN");
});

function image(id: string, role: DesignImage["role"], parent: string | null = null): DesignImage {
  return { id, role, job_id: "", url: `/api/generated/${id}.webp`, width: 800, height: 1100, instructions: "", parent_id: parent, source: "agent" };
}

function conversation(images: DesignImage[], ids: Partial<Conversation["state"]> = {}): Conversation {
  return {
    id: "p",
    title: "demo",
    created_at: "",
    locale: "zh-CN",
    status: "idle",
    running: false,
    last_seq: 0,
    events: [],
    state: {
      references: [],
      images,
      current_design_id: null,
      approved_design_id: null,
      current_front_id: null,
      approved_front_id: null,
      ...ids,
    },
  };
}

describe("projectStages", () => {
  it("starts in the design stage", () => {
    expect(projectStages(conversation([])).map((stage) => stage.state)).toEqual(["active", "todo", "todo"]);
  });

  it("moves to the head shell once the design is approved", () => {
    const stages = projectStages(
      conversation([image("design-1", "design"), image("front-1", "front", "design-1")], {
        approved_design_id: "design-1",
        current_design_id: "design-1",
        current_front_id: "front-1",
      }),
    );
    expect(stages.map((stage) => stage.state)).toEqual(["done", "active", "todo"]);
    expect(stages.map((stage) => stage.imageId)).toEqual(["design-1", "front-1", null]);
  });

  it("marks the design stage skipped when the head shell came straight from references", () => {
    const stages = projectStages(
      conversation([image("front-1", "front"), image("turnaround-1", "turnaround", "front-1")], {
        approved_front_id: "front-1",
        current_front_id: "front-1",
      }),
    );
    expect(stages.map((stage) => stage.state)).toEqual(["skipped", "done", "done"]);
  });
});

describe("ChatTimeline approvals", () => {
  function imageEvent(seq: number, imageId: string, role: string): AgentEvent {
    return { seq, type: "image", image_id: imageId, role, url: `/x/${imageId}.webp`, width: 800, height: 1100 } as AgentEvent;
  }

  it("offers 确认设定图 on the newest design and the normal approve on the newest head shell front", () => {
    const onApprove = vi.fn();
    const events = [imageEvent(1, "design-1", "design"), imageEvent(2, "design-2", "design"), imageEvent(3, "front-1", "front")];
    render(
      <ChatTimeline
        conversation={conversation([], { approved_design_id: null })}
        events={events}
        onApprove={onApprove}
        onOpenImage={vi.fn()}
        running={false}
      />,
    );
    const designButtons = screen.getAllByRole("button", { name: /确认设定图/ });
    expect(designButtons).toHaveLength(1);
    fireEvent.click(designButtons[0]);
    expect(onApprove).toHaveBeenCalledWith("design-2");
    expect(screen.getAllByRole("button", { name: /就用这张/ })).toHaveLength(1);
  });

  it("hides the design approve button once that design is approved", () => {
    render(
      <ChatTimeline
        conversation={conversation([], { approved_design_id: "design-1" })}
        events={[imageEvent(1, "design-1", "design")]}
        onApprove={vi.fn()}
        onOpenImage={vi.fn()}
        running={false}
      />,
    );
    expect(screen.queryByRole("button", { name: /确认设定图/ })).toBeNull();
    expect(screen.getByText("已确认")).toBeTruthy();
  });
});
