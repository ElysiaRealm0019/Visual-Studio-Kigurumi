import {
  IconArrowLeft,
  IconDeviceFloppy,
  IconDownload,
  IconFiles,
  IconLayoutSidebarRight,
  IconPhoto,
  IconSparkles,
  IconX,
} from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import {
  AgentApiError,
  approveActionFor,
  cancelRun,
  isTurnaroundRole,
  prepareUpload,
  renameConversation,
  saveManualVersion,
  sendMessage,
  startAnnotatedRevision,
  startLocalRevision,
  uploadReferences,
  versionSourceUrl,
  type ApproveAction,
  type Conversation,
  type DesignImage,
} from "../agent/agentApi";
import { useConversation } from "../agent/useConversation";
import { editorTools, type EditorTool } from "../editor/components/EditorToolRail";
import {
  EditorWorkspace,
  type EditorImageSavePayload,
  type EditorLocalGeneratePayload,
  type EditorRegeneratePayload,
  type EditorStatus,
  type EditorWorkspaceHandle,
} from "../editor/EditorWorkspace";
import { LanguageSelector } from "../i18n/LanguageSelector";
import { ThemeToggle } from "./ThemeToggle";
import { AgentPanel } from "./AgentPanel";
import { ExplorerPanel } from "./ExplorerPanel";

type Activity = "explorer" | EditorTool;

const TURNAROUND_TOOLS: EditorTool[] = ["annotation", "liquify", "local-generate"];
const AGENT_OPEN_KEY = "kigcraft.agentPanel.v1";

export function WorkspacePage() {
  const { t } = useTranslation();
  const { projectId = null } = useParams();
  const queryClient = useQueryClient();
  const { conversation, events, running, setRunning, loadError, refresh } = useConversation(projectId);

  const [activity, setActivity] = useState<Activity>("explorer");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [agentOpen, setAgentOpen] = useState(() => window.localStorage.getItem(AGENT_OPEN_KEY) !== "0");
  const [editorTool, setEditorTool] = useState<EditorTool>("annotation");
  const [openTabs, setOpenTabs] = useState<string[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const [editorStatus, setEditorStatus] = useState<EditorStatus | null>(null);
  const [panelEl, setPanelEl] = useState<HTMLDivElement | null>(null);
  const [toolbarEl, setToolbarEl] = useState<HTMLDivElement | null>(null);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [sendError, setSendError] = useState<string | null>(null);
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);
  const [toast, setToast] = useState<{ text: string; tone?: "error" } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [saving, setSaving] = useState(false);
  const editorRef = useRef<EditorWorkspaceHandle | null>(null);
  const seenImageIds = useRef<Set<string> | null>(null);

  const images = conversation?.state.images ?? [];
  const activeVersion = images.find((image) => image.id === activeTabId) ?? null;
  const dirty = Boolean(editorStatus?.dirty);
  const availableTools = isTurnaroundRole(activeVersion?.role) ? TURNAROUND_TOOLS : undefined;

  useEffect(() => window.localStorage.setItem(AGENT_OPEN_KEY, agentOpen ? "1" : "0"), [agentOpen]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 3200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  // Reset per project.
  useEffect(() => {
    setOpenTabs([]);
    setActiveTabId(null);
    seenImageIds.current = null;
  }, [projectId]);

  const openVersion = useCallback(
    (imageId: string, { force = false }: { force?: boolean } = {}) => {
      if (!force && imageId !== activeTabId && editorRef.current?.isDirty() && !window.confirm(t("workspace.discardChanges"))) {
        return;
      }
      setOpenTabs((tabs) => (tabs.includes(imageId) ? tabs : [...tabs, imageId]));
      setActiveTabId(imageId);
    },
    [activeTabId, t],
  );

  // Open the current version on load, then follow new versions as they arrive (unless the user is mid-edit).
  useEffect(() => {
    if (!conversation) return;
    const ids = conversation.state.images.map((image) => image.id);
    if (seenImageIds.current === null) {
      seenImageIds.current = new Set(ids);
      const initial = conversation.state.current_front_id ?? conversation.state.current_design_id ?? ids[ids.length - 1];
      if (initial) openVersion(initial, { force: true });
      return;
    }
    const fresh = ids.filter((id) => !seenImageIds.current?.has(id));
    fresh.forEach((id) => seenImageIds.current?.add(id));
    const newest = fresh[fresh.length - 1];
    if (!newest) return;
    if (editorRef.current?.isDirty()) {
      setOpenTabs((tabs) => (tabs.includes(newest) ? tabs : [...tabs, newest]));
    } else {
      openVersion(newest, { force: true });
    }
  }, [conversation, openVersion]);

  useEffect(() => {
    // Keep the active tool valid for the open version (turnarounds only support a subset).
    if (availableTools && !availableTools.includes(editorTool)) setEditorTool(availableTools[0]);
  }, [availableTools, editorTool]);

  function closeTab(imageId: string) {
    if (imageId === activeTabId && editorRef.current?.isDirty() && !window.confirm(t("workspace.discardChanges"))) return;
    setOpenTabs((tabs) => {
      const next = tabs.filter((id) => id !== imageId);
      if (imageId === activeTabId) setActiveTabId(next[next.length - 1] ?? null);
      return next;
    });
  }

  function selectActivity(next: Activity) {
    if (next === activity) {
      setSidebarOpen((open) => !open);
      return;
    }
    setActivity(next);
    setSidebarOpen(true);
    if (next !== "explorer") setEditorTool(next);
  }

  function reportError(error: unknown) {
    if (error instanceof AgentApiError && error.status === 409) {
      setToast({ text: t("workspace.busy"), tone: "error" });
      return;
    }
    const message = error instanceof Error ? error.message : String(error);
    setToast({ text: t("agent.errors.generic", { message }), tone: "error" });
  }

  async function sendChat(text: string, files: File[], extra: { action?: ApproveAction; imageId?: string } = {}) {
    if (!projectId) return false;
    setSendError(null);
    try {
      const prepared = await Promise.all(files.map((file) => prepareUpload(file)));
      setRunning(true);
      await sendMessage(projectId, { text, files: prepared, ...extra });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      return true;
    } catch (error) {
      setRunning(false);
      const code = error instanceof AgentApiError ? error.message : String(error);
      setSendError(t(`agent.errors.${code}`, { defaultValue: t("agent.errors.generic", { message: code }) }));
      return false;
    }
  }

  async function addReferences(files: File[]) {
    if (!projectId) return;
    try {
      const prepared = await Promise.all(files.map((file) => prepareUpload(file)));
      await uploadReferences(projectId, prepared);
      await refresh();
    } catch (error) {
      reportError(error);
    }
  }

  async function handleSave(payload: EditorImageSavePayload) {
    if (!projectId || !activeVersion) return;
    setSaving(true);
    try {
      const updated = await saveManualVersion(projectId, {
        baseImageId: activeVersion.id,
        image: payload.imageBlob,
        note: payload.annotationPrompt,
        recipe: payload.recipe,
      });
      const created = updated.state.images[updated.state.images.length - 1];
      seenImageIds.current?.add(created.id);
      await refresh();
      openVersion(created.id, { force: true });
      setToast({ text: t("workspace.savedVersion", { id: created.id }) });
    } catch (error) {
      reportError(error);
    } finally {
      setSaving(false);
    }
  }

  async function handleLocalGenerate(payload: EditorLocalGeneratePayload) {
    if (!projectId || !activeVersion) return;
    try {
      setRunning(true);
      await startLocalRevision(projectId, {
        baseImageId: activeVersion.id,
        baseImage: payload.baseImageBlob,
        mask: payload.maskImageBlob,
        editNote: payload.editNote,
        lockOutside: Boolean(payload.lockOutside),
        referenceIds: payload.selectedReferenceKeys,
        referenceFile: payload.uploadedReferences[0]?.file,
      });
      setAgentOpen(true);
    } catch (error) {
      setRunning(false);
      reportError(error);
    }
  }

  async function handleAnnotatedRegenerate(payload: EditorRegeneratePayload) {
    if (!projectId || !activeVersion) return;
    try {
      setRunning(true);
      await startAnnotatedRevision(projectId, {
        baseImageId: activeVersion.id,
        image: payload.annotatedImageBlob ?? payload.editedImageBlob,
        instructions: [payload.annotationPrompt, payload.promptNote].filter(Boolean).join("\n"),
      });
      setAgentOpen(true);
    } catch (error) {
      setRunning(false);
      reportError(error);
    }
  }

  async function saveCurrent() {
    if (!editorRef.current || !activeVersion || running || saving) return;
    await editorRef.current.save();
  }

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void saveCurrent();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (running) return;
    const files = [...event.dataTransfer.files].filter((file) => file.type.startsWith("image/"));
    if (!files.length) return;
    setAgentOpen(true);
    setPendingFiles((current) => [...current, ...files].slice(0, 6));
  }

  const localReferenceOptions = useMemo(
    () =>
      (conversation?.state.references ?? []).map((reference) => ({
        key: reference.id,
        label: reference.file_name,
        imageUrl: reference.url,
      })),
    [conversation?.state.references],
  );

  const runState = conversation?.status === "error" ? "error" : running ? "running" : "idle";

  return (
    <div
      className="ide-root"
      onDragLeave={(event) => {
        if (event.currentTarget === event.target) setDragging(false);
      }}
      onDragOver={(event) => {
        if ([...event.dataTransfer.types].includes("Files")) {
          event.preventDefault();
          setDragging(true);
        }
      }}
      onDrop={onDrop}
    >
      <TitleBar
        agentOpen={agentOpen}
        conversation={conversation}
        onRename={async (title) => {
          if (!projectId) return;
          await renameConversation(projectId, title);
          await refresh();
          void queryClient.invalidateQueries({ queryKey: ["projects"] });
        }}
        onOpenVersion={(id) => openVersion(id)}
        onToggleAgent={() => setAgentOpen((open) => !open)}
      />

      <div className="ide-body">
        <nav className="ide-activity">
          <ActivityItem
            active={activity === "explorer" && sidebarOpen}
            icon={<IconFiles size={20} />}
            label={t("workspace.explorerShort")}
            title={t("workspace.explorer")}
            onClick={() => selectActivity("explorer")}
          />
          <div className="ide-activity-divider" />
          {editorTools.map((tool) => {
            const Icon = tool.icon;
            const enabled = Boolean(activeVersion) && (!availableTools || availableTools.includes(tool.key));
            return (
              <ActivityItem
                active={activity === tool.key && sidebarOpen}
                disabled={!enabled}
                icon={<Icon size={19} />}
                key={tool.key}
                label={t(`workspace.tools.${tool.key}`)}
                onClick={() => selectActivity(tool.key)}
              />
            );
          })}
        </nav>

        {sidebarOpen ? (
          <aside className="ide-sidebar">
            {activity === "explorer" ? (
              <ExplorerPanel
                activeVersionId={activeTabId}
                conversation={conversation}
                disabled={running}
                onAddReferences={(files) => void addReferences(files)}
                onOpenImage={setLightboxUrl}
                onOpenVersion={(id) => openVersion(id)}
              />
            ) : (
              <>
                <div className="ide-sidebar-header">{t(`workspace.tools.${activity}`)}</div>
                <div className="ide-sidebar-scroll">
                  {activeVersion ? null : <p className="ide-empty">{t("workspace.openVersionFirst")}</p>}
                  {isTurnaroundRole(activeVersion?.role) ? (
                    <p className="ide-empty">{t("workspace.turnaroundTools")}</p>
                  ) : null}
                  <div ref={setPanelEl} />
                </div>
              </>
            )}
          </aside>
        ) : null}

        <section className="ide-editor">
          <div className="ide-tabs" role="tablist">
            {openTabs.map((id) => {
              const image = images.find((item) => item.id === id);
              if (!image) return null;
              const isActive = id === activeTabId;
              return (
                <div className="ide-tab" data-active={isActive} key={id} role="tab" aria-selected={isActive}>
                  <button className="flex items-center gap-1.5" onClick={() => openVersion(id)} type="button">
                    <IconPhoto size={13} />
                    {id}
                    <span className="text-[var(--ide-text-faint)]">{t(`workspace.roles.${image.role}`)}</span>
                  </button>
                  {isActive && dirty ? <span className="ide-tab-dirty" title={t("workspace.unsaved")} /> : null}
                  <button aria-label={t("workspace.closeTab")} className="ide-tab-close" onClick={() => closeTab(id)} type="button">
                    <IconX size={12} />
                  </button>
                </div>
              );
            })}
          </div>

          {activeVersion && projectId ? (
            <>
              <div className="ide-toolbar">
                <div className="min-w-0 flex-1" ref={setToolbarEl} />
                <div className="ide-toolbar-actions">
                  {editorTool === "annotation" && (activeVersion.role === "front" || activeVersion.role === "design") ? (
                    <button
                      className="ide-button"
                      disabled={running}
                      onClick={() => void editorRef.current?.regenerate()}
                      title={t("workspace.regenerateFromAnnotations")}
                      type="button"
                    >
                      <IconSparkles size={14} />
                      {t("workspace.regenerateFromAnnotations")}
                    </button>
                  ) : null}
                  <a className="ide-icon-button" download href={activeVersion.url} title={t("workspace.download")}>
                    <IconDownload size={16} />
                  </a>
                  <button
                    className="ide-button ide-button-primary"
                    disabled={!dirty || running || saving}
                    onClick={() => void saveCurrent()}
                    title="Ctrl+S"
                    type="button"
                  >
                    <IconDeviceFloppy size={14} />
                    {saving ? t("workspace.saving") : t("workspace.saveVersion")}
                  </button>
                </div>
              </div>
              <div className="ide-canvas">
                <EditorWorkspace
                  activeTool={availableTools && !availableTools.includes(editorTool) ? availableTools[0] : editorTool}
                  availableTools={availableTools}
                  candidateIndex={1}
                  downloadOnSave={false}
                  imageHeight={activeVersion.height}
                  imageUrl={versionSourceUrl(projectId, activeVersion.id)}
                  imageWidth={activeVersion.width}
                  isRegenerating={running}
                  key={activeVersion.id}
                  layout="ide"
                  localReferenceOptions={localReferenceOptions}
                  onActiveToolChange={setEditorTool}
                  onLocalGenerate={handleLocalGenerate}
                  onRegenerate={handleAnnotatedRegenerate}
                  onSave={handleSave}
                  onStatusChange={setEditorStatus}
                  panelTarget={activity !== "explorer" && sidebarOpen ? panelEl : null}
                  ref={editorRef}
                  showRegenerateActions={false}
                  toolbarTarget={toolbarEl}
                />
              </div>
            </>
          ) : (
            <div className="ide-welcome">
              {loadError ? (
                <p className="agent-notice agent-notice-error">{t("agent.errors.load", { message: loadError })}</p>
              ) : (
                <>
                  <img alt="" className="h-12 w-12 opacity-60" src="/logo.png" />
                  <p className="max-w-sm text-sm">{images.length ? t("workspace.openVersionFirst") : t("workspace.noVersions")}</p>
                </>
              )}
            </div>
          )}
          {dragging ? <div className="ide-dropzone">{t("agent.dropHint")}</div> : null}
        </section>

        {agentOpen ? (
          <AgentPanel
            conversation={conversation}
            events={events}
            onApprove={(imageId) => {
              const action = approveActionFor(images.find((image) => image.id === imageId)?.role);
              if (action) void sendChat("", [], { action, imageId });
            }}
            onFilesChange={setPendingFiles}
            onOpenImage={setLightboxUrl}
            onOpenVersion={(id) => openVersion(id)}
            onSend={(text, files) => sendChat(text, files)}
            onStop={() => projectId && void cancelRun(projectId)}
            pendingFiles={pendingFiles}
            running={running}
            sendError={sendError}
          />
        ) : null}
      </div>

      <StatusBar activeVersion={activeVersion} editorStatus={activeVersion ? editorStatus : null} runState={runState} />

      {lightboxUrl ? (
        <div className="ide-lightbox" onClick={() => setLightboxUrl(null)} role="presentation">
          <img alt="" src={lightboxUrl} />
        </div>
      ) : null}
      {toast ? (
        <div className="ide-toast" data-tone={toast.tone} role="status">
          {toast.text}
        </div>
      ) : null}
    </div>
  );
}

function ActivityItem({
  active,
  disabled = false,
  icon,
  label,
  title,
  onClick,
}: {
  active: boolean;
  disabled?: boolean;
  icon: React.ReactNode;
  label: string;
  title?: string;
  onClick: () => void;
}) {
  return (
    <button
      aria-label={title ?? label}
      aria-pressed={active}
      className="ide-activity-item"
      data-active={active}
      disabled={disabled}
      onClick={onClick}
      title={title ?? label}
      type="button"
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

type StageState = "done" | "active" | "todo" | "skipped";

/** Where the project is in the two-stage flow: character design -> head shell front -> head shell four-view. */
export function projectStages(conversation: Conversation | null) {
  const state = conversation?.state;
  const images = state?.images ?? [];
  const latest = (roles: string[]) => [...images].reverse().find((image) => roles.includes(image.role))?.id ?? null;
  const designId = state?.approved_design_id ?? state?.current_design_id ?? latest(["design", "design_turnaround"]);
  const frontId = state?.approved_front_id ?? state?.current_front_id ?? latest(["front"]);
  const turnaroundId = latest(["turnaround"]);
  const designDone = Boolean(state?.approved_design_id);
  const frontDone = Boolean(state?.approved_front_id);
  const stage = (done: boolean, reached: boolean): StageState => (done ? "done" : reached ? "active" : "todo");
  return [
    {
      key: "design" as const,
      imageId: designId,
      // Head shell made straight from the references: the design stage was skipped.
      state: designDone ? "done" : !designId && frontId ? "skipped" : ("active" as StageState),
    },
    { key: "front" as const, imageId: frontId, state: stage(frontDone, designDone || Boolean(frontId)) },
    { key: "turnaround" as const, imageId: turnaroundId, state: turnaroundId ? "done" : stage(false, frontDone) },
  ];
}

function StageTrack({ conversation, onOpenVersion }: { conversation: Conversation | null; onOpenVersion: (id: string) => void }) {
  const { t } = useTranslation();
  return (
    <ol aria-label={t("workspace.stageHint")} className="ide-stages">
      {projectStages(conversation).map((stage, index) => (
        <li data-state={stage.state} key={stage.key}>
          {index > 0 ? <span aria-hidden className="ide-stage-arrow" /> : null}
          <button
            disabled={!stage.imageId}
            onClick={() => stage.imageId && onOpenVersion(stage.imageId)}
            title={t("workspace.stageHint")}
            type="button"
          >
            <span className="ide-stage-index">{stage.state === "done" ? "✓" : index + 1}</span>
            {t(`workspace.stages.${stage.key}`)}
          </button>
        </li>
      ))}
    </ol>
  );
}

function TitleBar({
  conversation,
  agentOpen,
  onToggleAgent,
  onRename,
  onOpenVersion,
}: {
  conversation: Conversation | null;
  agentOpen: boolean;
  onToggleAgent: () => void;
  onRename: (title: string) => Promise<void>;
  onOpenVersion: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");

  useEffect(() => setDraft(conversation?.title ?? ""), [conversation?.title]);

  function commit() {
    const title = draft.trim();
    if (title && title !== conversation?.title) void onRename(title);
    else setDraft(conversation?.title ?? "");
  }

  return (
    <header className="ide-titlebar">
      <Link aria-label={t("workspace.home")} className="ide-icon-button" title={t("workspace.home")} to="/">
        <IconArrowLeft size={16} />
      </Link>
      <img alt="KigCraft" className="h-5 w-5" src="/logo.png" />
      <div className="ide-titlebar-title">
        <Link className="hover:text-[var(--ide-text)]" to="/">
          {t("workspace.projects")}
        </Link>
        <span>/</span>
        <input
          aria-label={t("workspace.rename")}
          onBlur={commit}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") (event.target as HTMLInputElement).blur();
            if (event.key === "Escape") {
              setDraft(conversation?.title ?? "");
              (event.target as HTMLInputElement).blur();
            }
          }}
          placeholder={t("workspace.untitled")}
          size={Math.max(8, draft.length + 2)}
          value={draft}
        />
      </div>
      <StageTrack conversation={conversation} onOpenVersion={onOpenVersion} />
      <button
        aria-label={t("workspace.toggleAgent")}
        className="ide-icon-button"
        data-active={agentOpen}
        onClick={onToggleAgent}
        title={t("workspace.toggleAgent")}
        type="button"
      >
        <IconLayoutSidebarRight size={16} />
      </button>
      <ThemeToggle />
      <LanguageSelector compact />
    </header>
  );
}

function StatusBar({
  runState,
  activeVersion,
  editorStatus,
}: {
  runState: "idle" | "running" | "error";
  activeVersion: DesignImage | null;
  editorStatus: EditorStatus | null;
}) {
  const { t } = useTranslation();
  return (
    <footer className="ide-statusbar">
      <span className="ide-statusbar-item">
        <span className="ide-statusbar-dot" data-state={runState} />
        {runState === "running" ? t("workspace.status.running") : t("workspace.status.idle")}
      </span>
      {activeVersion ? (
        <span className="ide-statusbar-item">
          {activeVersion.id} · {t(`workspace.roles.${activeVersion.role}`)} · {activeVersion.width}×{activeVersion.height} ·{" "}
          {t(`workspace.sources.${activeVersion.source}`)}
        </span>
      ) : null}
      <span className="flex-1" />
      {editorStatus ? (
        <>
          <span className="ide-statusbar-item">
            {editorStatus.detectingLandmarks
              ? t("workspace.status.detecting")
              : editorStatus.landmarksReady
                ? t("workspace.status.landmarksReady")
                : t("workspace.status.noLandmarks")}
          </span>
          <span className="ide-statusbar-item">{t("workspace.status.zoom", { value: editorStatus.zoomPercent })}</span>
        </>
      ) : null}
    </footer>
  );
}
