import {
  IconAlertTriangle,
  IconCheck,
  IconExternalLink,
  IconLoader2,
  IconPhotoPlus,
  IconRefresh,
  IconSparkles,
  IconTrash,
  IconX,
} from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import { approveActionFor, isDesignRole, isTurnaroundRole, type AgentEvent, type Conversation } from "./agentApi";

type TimelineProps = {
  events: AgentEvent[];
  conversation: Conversation | null;
  running: boolean;
  onApprove: (imageId: string) => void;
  onOpenImage: (url: string) => void;
  /** Open a version in the editor (IDE workspace). Falls back to the lightbox when absent. */
  onOpenVersion?: (imageId: string) => void;
  /** Delete an assistant reply (timeline and model context). */
  onDeleteMessage?: (seq: number) => void;
  /** Run the last user turn again. Offered on the newest reply only. */
  onRegenerate?: () => void;
};

const TOOL_KEYS = new Set([
  "analyze_references",
  "generate_design",
  "revise_design",
  "approve_design",
  "generate_front_view",
  "revise_front_view",
  "approve_front_view",
  "generate_turnaround",
  "local_revision",
  "annotated_revision",
]);

export function ChatTimeline({
  events: allEvents,
  conversation,
  running,
  onApprove,
  onOpenImage,
  onOpenVersion,
  onDeleteMessage,
  onRegenerate,
}: TimelineProps) {
  const { t } = useTranslation();
  const events = allEvents.filter((event) => !event.deleted);
  const approvedIds = new Set(
    [conversation?.state.approved_design_id, conversation?.state.approved_front_id].filter(Boolean) as string[],
  );
  // Only the newest image of each approvable stage offers the approve button.
  const latestApprovable = new Map<string, string>();
  for (const event of events) {
    const action = event.type === "image" ? approveActionFor(event.role as string) : null;
    if (action) latestApprovable.set(action, event.image_id as string);
  }
  const lastEvent = events[events.length - 1];
  const lastReplySeq = events.filter((event) => event.type === "assistant_message").at(-1)?.seq;
  const waitingForModel = running && (lastEvent?.type === "user_message" || lastEvent?.type === "run_state");

  return (
    <ol className="flex flex-col gap-3" aria-live="polite">
      {events.map((event) => {
        switch (event.type) {
          case "user_message":
            return <UserMessage event={event} key={event.seq} onOpenImage={onOpenImage} />;
          case "references_added":
            return <ReferencesAdded event={event} key={event.seq} onOpenImage={onOpenImage} />;
          case "assistant_message":
            return (
              <li className="flex gap-2" key={event.seq}>
                <Avatar />
                <div className="min-w-0 flex-1">
                  <div className="agent-bubble whitespace-pre-wrap break-words">{event.text as string}</div>
                  {!running && (onDeleteMessage || onRegenerate) ? (
                    <div className="agent-message-actions">
                      {onRegenerate && event.seq === lastReplySeq ? (
                        <button
                          aria-label={t("agent.regenerate")}
                          className="agent-message-action"
                          onClick={onRegenerate}
                          title={t("agent.regenerate")}
                          type="button"
                        >
                          <IconRefresh size={13} />
                        </button>
                      ) : null}
                      {onDeleteMessage ? (
                        <button
                          aria-label={t("agent.deleteMessage")}
                          className="agent-message-action"
                          onClick={() => onDeleteMessage(event.seq)}
                          title={t("agent.deleteMessage")}
                          type="button"
                        >
                          <IconTrash size={13} />
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </li>
            );
          case "tool":
            return <ToolCard event={event} key={event.seq} />;
          case "image":
            return (
              <ImageCard
                approved={approvedIds.has(event.image_id as string)}
                canApprove={
                  !running &&
                  latestApprovable.get(approveActionFor(event.role as string) ?? "") === event.image_id &&
                  !approvedIds.has(event.image_id as string)
                }
                event={event}
                key={event.seq}
                onApprove={onApprove}
                onOpenImage={onOpenImage}
                onOpenVersion={onOpenVersion}
              />
            );
          case "error":
            return (
              <li className="agent-notice agent-notice-error" key={event.seq}>
                <IconAlertTriangle className="shrink-0" size={14} />
                <span className="min-w-0 break-words">{t("agent.errors.generic", { message: event.message as string })}</span>
              </li>
            );
          case "notice": {
            const code = event.code as string;
            return (
              <li className="agent-notice" key={event.seq}>
                {t(`agent.notices.${code}`, { defaultValue: code })}
              </li>
            );
          }
          default:
            return null;
        }
      })}
      {waitingForModel ? (
        <li className="flex items-center gap-2 text-xs text-[var(--ide-text-muted)]">
          <Avatar />
          <IconLoader2 className="animate-spin" size={14} />
          {t("agent.thinking")}
        </li>
      ) : null}
    </ol>
  );
}

function Avatar() {
  return (
    <span className="agent-avatar" aria-hidden>
      <IconSparkles size={14} />
    </span>
  );
}

function UserMessage({ event, onOpenImage }: { event: AgentEvent; onOpenImage: (url: string) => void }) {
  const { t } = useTranslation();
  const attachments = (event.attachments ?? []) as Array<{ id: string; url: string; kind: string }>;
  const action = event.action as string | null | undefined;
  const text = (event.text as string) || (action === "approve_front"
      ? t("agent.approveMessage")
      : action === "approve_design"
        ? t("agent.approveDesignMessage")
        : "");
  const actionLabel =
    action === "local_revision" || action === "annotated_revision" ? t(`agent.actions.${action}`) : null;
  return (
    <li className="flex flex-col items-end gap-1.5">
      {attachments.length ? <Thumbnails attachments={attachments} onOpenImage={onOpenImage} /> : null}
      {text || actionLabel ? (
        <div className="agent-bubble agent-bubble-user max-w-[92%] whitespace-pre-wrap break-words">
          {actionLabel ? (
            <span className="mb-0.5 block text-[11px] font-semibold text-[var(--ide-accent)]">
              {actionLabel} · {event.image_id as string}
            </span>
          ) : null}
          {text}
        </div>
      ) : null}
    </li>
  );
}

function ReferencesAdded({ event, onOpenImage }: { event: AgentEvent; onOpenImage: (url: string) => void }) {
  const { t } = useTranslation();
  const attachments = (event.attachments ?? []) as Array<{ id: string; url: string; kind: string }>;
  return (
    <li className="flex flex-col items-end gap-1.5">
      <span className="flex items-center gap-1 text-[11px] text-[var(--ide-text-muted)]">
        <IconPhotoPlus size={12} />
        {t("agent.referencesAdded")}
      </span>
      <Thumbnails attachments={attachments} onOpenImage={onOpenImage} />
    </li>
  );
}

function Thumbnails({
  attachments,
  onOpenImage,
}: {
  attachments: Array<{ id: string; url: string; kind: string }>;
  onOpenImage: (url: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap justify-end gap-1.5">
      {attachments.map((attachment) => (
        <button
          className="agent-thumb"
          key={attachment.id}
          onClick={() => onOpenImage(attachment.url)}
          title={t("agent.openImage")}
          type="button"
        >
          <img alt="" src={attachment.url} />
          {attachment.kind === "front" ? <span className="agent-thumb-tag">{t("agent.referenceMain")}</span> : null}
        </button>
      ))}
    </div>
  );
}

function ToolCard({ event }: { event: AgentEvent }) {
  const { t } = useTranslation();
  const tool = event.tool as string;
  const status = (event.status as string) || "running";
  const progress = Math.max(0, Math.min(100, Number(event.progress ?? 0)));
  const label = TOOL_KEYS.has(tool) ? t(`agent.tools.${tool}`) : tool;
  return (
    <li className="agent-tool ml-8 flex flex-col gap-1.5" data-status={status}>
      <div className="flex items-center gap-1.5 text-xs">
        {status === "running" ? <IconLoader2 className="animate-spin" size={14} /> : null}
        {status === "succeeded" ? <IconCheck size={14} /> : null}
        {status === "failed" ? <IconX className="text-[var(--ide-danger)]" size={14} /> : null}
        <span className="font-semibold">{label}</span>
        <span className="truncate text-[var(--ide-text-muted)]">
          · {status === "running" && event.phase ? (event.phase as string) : t(`agent.toolStatus.${status}`)}
        </span>
      </div>
      {status === "running" && progress > 0 ? (
        <div className="agent-progress" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
          <span style={{ width: `${progress}%` }} />
        </div>
      ) : null}
      {status === "failed" && event.error ? (
        <p className="break-words text-[11px] text-[var(--ide-danger)]">{event.error as string}</p>
      ) : null}
    </li>
  );
}

function ImageCard({
  event,
  approved,
  canApprove,
  onApprove,
  onOpenImage,
  onOpenVersion,
}: {
  event: AgentEvent;
  approved: boolean;
  canApprove: boolean;
  onApprove: (imageId: string) => void;
  onOpenImage: (url: string) => void;
  onOpenVersion?: (imageId: string) => void;
}) {
  const { t } = useTranslation();
  const role = event.role as string;
  const isTurnaround = isTurnaroundRole(role);
  const url = event.url as string;
  const imageId = event.image_id as string;
  const source = (event.source as string) || "agent";
  const open = () => (onOpenVersion ? onOpenVersion(imageId) : onOpenImage(url));
  return (
    <li className={`agent-image-card ml-8 ${isTurnaround ? "" : "max-w-[15rem]"}`}>
      <button className="block w-full" onClick={open} title={onOpenVersion ? t("agent.openInEditor") : t("agent.openImage")} type="button">
        <img
          alt={t(`workspace.roles.${role}`)}
          className="block w-full"
          height={event.height as number}
          loading="lazy"
          src={url}
          width={event.width as number}
        />
      </button>
      <div className="agent-image-card-footer">
        <span className="text-xs font-semibold">{imageId}</span>
        <span className="ide-badge">{t(`workspace.roles.${role}`)}</span>
        <span className="ide-badge">{t(`workspace.sources.${source}`, { defaultValue: source })}</span>
        {approved ? (
          <span className="agent-badge">
            <IconCheck size={12} /> {t("agent.approved")}
          </span>
        ) : null}
        <span className="flex-1" />
        {onOpenVersion ? (
          <button className="agent-icon-button" onClick={open} title={t("agent.openInEditor")} type="button">
            <IconExternalLink size={14} />
          </button>
        ) : null}
        {canApprove ? (
          <button className="agent-button agent-button-primary" onClick={() => onApprove(imageId)} type="button">
            <IconCheck size={14} />
            {isDesignRole(role) ? t("agent.approveDesign") : t("agent.approve")}
          </button>
        ) : null}
      </div>
    </li>
  );
}
