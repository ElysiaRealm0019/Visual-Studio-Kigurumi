import { useCallback, useEffect, useRef, useState } from "react";
import {
  applyEvent,
  getConversation,
  subscribeToEvents,
  type AgentEvent,
  type Conversation,
} from "./agentApi";

/** While a run is in progress, re-read the snapshot this often in case a live event was missed. */
const RUNNING_POLL_MS = 8000;

/** Loads a conversation and keeps its events live via SSE. */
export function useConversation(conversationId: string | null) {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const lastSeq = useRef(0);

  /** Re-read the snapshot. Responses older than what the page already has (a slow request overtaken by live
   *  events) must not roll the run state back, or the page stays on "thinking" after the run ended. */
  const refresh = useCallback(async () => {
    if (!conversationId) return;
    const next = await getConversation(conversationId);
    setConversation(next);
    if (next.last_seq < lastSeq.current) return;
    // The snapshot has every patch applied (progress, deleted replies), so it replaces the list outright.
    setEvents(next.events as AgentEvent[]);
    lastSeq.current = next.last_seq;
    setRunning(next.running);
  }, [conversationId]);

  useEffect(() => {
    setConversation(null);
    setEvents([]);
    setRunning(false);
    setLoadError(null);
    lastSeq.current = 0;
    if (!conversationId) return;

    let unsubscribe: (() => void) | null = null;
    let cancelled = false;
    void getConversation(conversationId)
      .then((loaded) => {
        if (cancelled) return;
        setConversation(loaded);
        setRunning(loaded.running);
        const loadedEvents = loaded.events as AgentEvent[];
        setEvents(loadedEvents);
        // The snapshot already has every progress patch applied; resume strictly after it.
        lastSeq.current = loaded.last_seq;
        unsubscribe = subscribeToEvents(
          conversationId,
          () => lastSeq.current,
          (event) => {
            lastSeq.current = Math.max(lastSeq.current, event.seq);
            setEvents((current) => applyEvent(current, event));
            if (event.type === "run_state") {
              setRunning(event.status === "running");
              if (event.status !== "running") void refresh();
            }
            if (event.type === "image" || event.type === "front_approved" || event.type === "design_approved") void refresh();
          },
          () => void refresh().catch(() => undefined),
        );
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : String(error));
      });

    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, [conversationId, refresh]);

  useEffect(() => {
    if (!running || !conversationId) return;
    const timer = setInterval(() => void refresh().catch(() => undefined), RUNNING_POLL_MS);
    return () => clearInterval(timer);
  }, [running, conversationId, refresh]);

  return { conversation, events, running, setRunning, loadError, refresh };
}
