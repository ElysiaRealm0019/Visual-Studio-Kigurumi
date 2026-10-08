import { useCallback, useEffect, useRef, useState } from "react";
import {
  applyEvent,
  getConversation,
  subscribeToEvents,
  type AgentEvent,
  type Conversation,
} from "./agentApi";

/** Loads a conversation and keeps its events live via SSE. */
export function useConversation(conversationId: string | null) {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const lastSeq = useRef(0);

  const refresh = useCallback(async () => {
    if (!conversationId) return;
    const next = await getConversation(conversationId);
    setConversation(next);
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
        unsubscribe = subscribeToEvents(conversationId, lastSeq.current, (event) => {
          lastSeq.current = Math.max(lastSeq.current, event.seq);
          setEvents((current) => applyEvent(current, event));
          if (event.type === "run_state") {
            setRunning(event.status === "running");
            if (event.status !== "running") void refresh();
          }
          if (event.type === "image" || event.type === "front_approved" || event.type === "design_approved") void refresh();
        });
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : String(error));
      });

    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, [conversationId, refresh]);

  return { conversation, events, running, setRunning, loadError, refresh };
}
