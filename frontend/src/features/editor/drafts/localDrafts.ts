import { normalizeEditRecipe, type EditRecipe } from "../deformation/recipe";
import type { LocalMaskStroke } from "../localGeneration";

/** Unsaved editor state of one version. Lives only in this browser (IndexedDB), never on the server. */
export type EditorDraftState = {
  recipe: EditRecipe;
  maskStrokes: LocalMaskStroke[];
  localEditNote: string;
};

export type EditorDraft = EditorDraftState & {
  schemaVersion: 1;
  key: string;
  projectId: string;
  versionId: string;
  updatedAt: number;
};

const databaseName = "vsk-editor-drafts";
const storeName = "drafts";

function draftKey(projectId: string, versionId: string) {
  return JSON.stringify([projectId, versionId]);
}

function open(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(databaseName, 1);
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore(storeName, { keyPath: "key" });
      store.createIndex("projectId", "projectId");
    };
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error("Draft database is blocked"));
    request.onsuccess = () => {
      request.result.onversionchange = () => request.result.close();
      resolve(request.result);
    };
  });
}

async function run<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest | null, fallback: T): Promise<T> {
  const db = await open();
  if (!db) return fallback;
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, mode);
    let request: IDBRequest | null = null;
    tx.oncomplete = () => {
      db.close();
      resolve(request ? (request.result as T) : fallback);
    };
    tx.onabort = tx.onerror = () => {
      db.close();
      reject(tx.error ?? new Error("Draft transaction failed"));
    };
    request = action(tx.objectStore(storeName));
  });
}

/** True when the state holds anything worth keeping; landmark detection alone is not an edit. */
export function draftHasContent(state: EditorDraftState, recipeHasEdits: (recipe: EditRecipe) => boolean) {
  return recipeHasEdits(state.recipe) || state.maskStrokes.length > 0 || state.localEditNote.trim() !== "";
}

export const editorDrafts = {
  async get(projectId: string, versionId: string): Promise<EditorDraft | undefined> {
    const record = await run<EditorDraft | undefined>("readonly", (store) => store.get(draftKey(projectId, versionId)), undefined);
    if (!record || record.schemaVersion !== 1) return undefined;
    return { ...record, recipe: normalizeEditRecipe(record.recipe) };
  },
  async put(projectId: string, versionId: string, state: EditorDraftState): Promise<void> {
    const record: EditorDraft = {
      schemaVersion: 1,
      key: draftKey(projectId, versionId),
      projectId,
      versionId,
      updatedAt: Date.now(),
      ...structuredClone(state),
    };
    await run("readwrite", (store) => store.put(record), undefined);
  },
  async remove(projectId: string, versionId: string): Promise<void> {
    await run("readwrite", (store) => store.delete(draftKey(projectId, versionId)), undefined);
  },
  async versionIds(projectId: string): Promise<string[]> {
    const records = await run<EditorDraft[]>("readonly", (store) => store.index("projectId").getAll(projectId), []);
    return records.map((record) => record.versionId);
  },
};
