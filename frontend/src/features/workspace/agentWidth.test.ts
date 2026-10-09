import { describe, expect, it } from "vitest";
import { clampAgentWidth, DEFAULT_AGENT_WIDTH, MIN_AGENT_WIDTH } from "./agentWidth";

describe("clampAgentWidth", () => {
  it("keeps widths inside the min/max bounds", () => {
    expect(clampAgentWidth(120, 1440)).toBe(MIN_AGENT_WIDTH);
    expect(clampAgentWidth(900, 1440)).toBe(720);
    expect(clampAgentWidth(460, 1440)).toBe(460);
  });

  it("reserves editor space on narrow screens", () => {
    // 900px screen - 460px for the editor area caps the panel, but never below the minimum.
    expect(clampAgentWidth(600, 900)).toBe(440);
    expect(clampAgentWidth(600, 700)).toBe(MIN_AGENT_WIDTH);
  });

  it("falls back to the default on garbage input and rounds fractions", () => {
    expect(clampAgentWidth(Number.NaN, 1440)).toBe(DEFAULT_AGENT_WIDTH);
    expect(clampAgentWidth(460.4, 1440)).toBe(460);
  });
});
