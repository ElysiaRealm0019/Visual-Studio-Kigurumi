import { useCallback, useEffect, useRef, useState } from "react";
import { draftHasContent, editorDrafts, type EditorDraft, type EditorDraftState } from "../editor/drafts/localDrafts";
import { recipeHasEdits } from "../editor/EditorWorkspace";

const WRITE_DELAY_MS = 400;

type Loaded = { versionId: string; draft: EditorDraft | null };
type Pending = { projectId: string; versionId: string; state: EditorDraftState };

/**
 * Keeps each version's unsaved editor state in IndexedDB, so switching versions or reloading the page does not
 * lose edits. `loaded` is the draft of the active version once read; mount the editor only after that.
 */
export function useEditorDrafts(projectId: string | null, versionId: string | null) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [draftIds, setDraftIds] = useState<ReadonlySet<string>>(new Set());
  const draftIdsRef = useRef<ReadonlySet<string>>(draftIds);
  const pendingRef = useRef<Pending | null>(null);
  const timerRef = useRef<number | null>(null);
  const writesRef = useRef<Promise<void>>(Promise.resolve());
  // Versions whose draft was just turned into a saved version; late changes from the closing editor are ignored.
  const discardedRef = useRef<Set<string>>(new Set());

  const updateIds = useCallback((update: (ids: Set<string>) => void) => {
    const next = new Set(draftIdsRef.current);
    update(next);
    draftIdsRef.current = next;
    setDraftIds(next);
  }, []);

  const enqueue = useCallback((work: () => Promise<void>) => {
    writesRef.current = writesRef.current.then(work).catch((error: unknown) => {
      console.warn("Editor draft write failed", error);
    });
  }, []);

  const flush = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    const pending = pendingRef.current;
    pendingRef.current = null;
    if (!pending || discardedRef.current.has(pending.versionId)) return;

    const { projectId: project, versionId: version, state } = pending;
    if (draftHasContent(state, recipeHasEdits)) {
      updateIds((ids) => ids.add(version));
      enqueue(() => editorDrafts.put(project, version, state));
    } else if (draftIdsRef.current.has(version)) {
      updateIds((ids) => ids.delete(version));
      enqueue(() => editorDrafts.remove(project, version));
    }
  }, [enqueue, updateIds]);

  /** Record the editor's current state; it is written shortly after the last change. */
  const schedule = useCallback(
    (state: EditorDraftState) => {
      if (!projectId || !loaded) return;
      if (pendingRef.current && pendingRef.current.versionId !== loaded.versionId) flush();
      pendingRef.current = { projectId, versionId: loaded.versionId, state };
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(flush, WRITE_DELAY_MS);
    },
    [flush, loaded, projectId],
  );

  /** Drop a version's draft, e.g. after it was saved as a new version. */
  const discard = useCallback(
    (version: string) => {
      if (!projectId) return;
      if (pendingRef.current?.versionId === version) {
        pendingRef.current = null;
        if (timerRef.current !== null) window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      discardedRef.current.add(version);
      updateIds((ids) => ids.delete(version));
      enqueue(() => editorDrafts.remove(projectId, version));
    },
    [enqueue, projectId, updateIds],
  );

  useEffect(() => {
    draftIdsRef.current = new Set();
    setDraftIds(new Set());
    if (!projectId) return;
    let cancelled = false;
    editorDrafts
      .versionIds(projectId)
      .then((ids) => {
        if (cancelled) return;
        draftIdsRef.current = new Set(ids);
        setDraftIds(draftIdsRef.current);
      })
      .catch((error: unknown) => console.warn("Editor drafts unavailable", error));
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    flush();
    setLoaded(null);
    if (!projectId || !versionId) return;
    discardedRef.current.delete(versionId);
    let cancelled = false;
    // Read after any queued write, so quickly coming back to a version sees its latest draft.
    writesRef.current
      .then(() => editorDrafts.get(projectId, versionId))
      .catch((error: unknown) => {
        console.warn("Editor draft read failed", error);
        return undefined;
      })
      .then((draft) => {
        if (!cancelled) setLoaded({ versionId, draft: draft ?? null });
      });
    return () => {
      cancelled = true;
    };
  }, [flush, projectId, versionId]);

  useEffect(() => {
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, [flush]);

  return { loaded: loaded && loaded.versionId === versionId ? loaded : null, draftIds, schedule, discard };
}
