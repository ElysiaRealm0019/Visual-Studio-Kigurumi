import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import { subscribeToEvents, type AgentEvent } from "./agentApi";
import { ChatTimeline } from "./ChatTimeline";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

beforeAll(async () => {
  await i18n.changeLanguage("zh-CN");
});

const reply = (seq: number, text: string, extra: Partial<AgentEvent> = {}): AgentEvent =>
  ({ seq, type: "assistant_message", created_at: "", text, ...extra }) as AgentEvent;

describe("assistant reply actions", () => {
  it("hides deleted replies and offers regenerate on the newest reply only", () => {
    const onDelete = vi.fn();
    const onRegenerate = vi.fn();
    render(
      <ChatTimeline
        conversation={null}
        events={[reply(1, "第一条"), reply(2, "被删掉的", { deleted: true }), reply(3, "最新一条")]}
        onApprove={vi.fn()}
        onDeleteMessage={onDelete}
        onOpenImage={vi.fn()}
        onRegenerate={onRegenerate}
        running={false}
      />,
    );
    expect(screen.queryByText("被删掉的")).toBeNull();
    expect(screen.getAllByRole("button", { name: "删除回复" })).toHaveLength(2);
    const regenerate = screen.getAllByRole("button", { name: "重新生成" });
    expect(regenerate).toHaveLength(1);
    fireEvent.click(regenerate[0]);
    expect(onRegenerate).toHaveBeenCalledOnce();
    fireEvent.click(screen.getAllByRole("button", { name: "删除回复" })[1]);
    expect(onDelete).toHaveBeenCalledWith(3);
  });

  it("hides the actions while a run is in progress", () => {
    render(
      <ChatTimeline
        conversation={null}
        events={[reply(1, "回复")]}
        onApprove={vi.fn()}
        onDeleteMessage={vi.fn()}
        onOpenImage={vi.fn()}
        onRegenerate={vi.fn()}
        running
      />,
    );
    expect(screen.queryByRole("button", { name: "重新生成" })).toBeNull();
    expect(screen.queryByRole("button", { name: "删除回复" })).toBeNull();
  });
});

class FakeEventSource {
  static CLOSED = 2;
  static instances: FakeEventSource[] = [];
  readyState = 1;
  listeners: Record<string, Array<(event: unknown) => void>> = {};
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: (event: unknown) => void) {
    (this.listeners[type] ??= []).push(listener);
  }
  emit(type: string, event: unknown = {}) {
    for (const listener of this.listeners[type] ?? []) listener(event);
  }
  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
}

describe("subscribeToEvents", () => {
  it("reopens a connection the browser gave up on and resumes after the last seen event", () => {
    vi.useFakeTimers();
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    let after = 5;
    const onReconnect = vi.fn();
    const unsubscribe = subscribeToEvents("p", () => after, vi.fn(), onReconnect);

    const first = FakeEventSource.instances[0];
    expect(first.url).toContain("after=5");
    first.emit("open");
    after = 9;
    first.readyState = FakeEventSource.CLOSED; // e.g. the backend answered 502 while restarting
    first.emit("error");
    vi.advanceTimersByTime(3000);

    const second = FakeEventSource.instances[1];
    expect(second.url).toContain("after=9");
    second.emit("open");
    expect(onReconnect).toHaveBeenCalledOnce();

    unsubscribe();
    second.readyState = FakeEventSource.CLOSED;
    second.emit("error");
    vi.advanceTimersByTime(3000);
    expect(FakeEventSource.instances).toHaveLength(2);
  });
});
