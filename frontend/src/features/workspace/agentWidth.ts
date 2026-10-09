/** Shared state for the resizable agent panel: a drag handle west of the panel sets its pixel width. */

export const DEFAULT_AGENT_WIDTH = 380;
export const MIN_AGENT_WIDTH = 300;
export const MAX_AGENT_WIDTH = 720;
/** Screen space the activity bar, explorer sidebar and a usable editor area need west of the agent panel. */
const EDITOR_MIN_WIDTH = 460;

export const AGENT_WIDTH_STORAGE_KEY = "kigcraft.agentPanelWidth.v1";

export function clampAgentWidth(width: number, innerWidth: number): number {
  if (!Number.isFinite(width)) return DEFAULT_AGENT_WIDTH;
  const max = Math.max(MIN_AGENT_WIDTH, Math.min(MAX_AGENT_WIDTH, innerWidth - EDITOR_MIN_WIDTH));
  return Math.round(Math.min(max, Math.max(MIN_AGENT_WIDTH, width)));
}

export function readStoredAgentWidth(innerWidth: number): number {
  const stored = Number(window.localStorage.getItem(AGENT_WIDTH_STORAGE_KEY));
  return Number.isFinite(stored) && stored > 0 ? clampAgentWidth(stored, innerWidth) : DEFAULT_AGENT_WIDTH;
}
