import { IconSparkles } from "@tabler/icons-react";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { AgentEvent, Conversation } from "../agent/agentApi";
import { ChatTimeline } from "../agent/ChatTimeline";
import { Composer } from "../agent/Composer";
import { DEFAULT_AGENT_WIDTH } from "./agentWidth";

type AgentPanelProps = {
  conversation: Conversation | null;
  events: AgentEvent[];
  running: boolean;
  pendingFiles: File[];
  sendError: string | null;
  width: number;
  onFilesChange: (files: File[]) => void;
  onSend: (text: string, files: File[]) => Promise<boolean>;
  onApprove: (imageId: string) => void;
  onStop: () => void;
  onOpenImage: (url: string) => void;
  onOpenVersion: (imageId: string) => void;
  onDeleteMessage: (seq: number) => void;
  onRegenerate: () => void;
};

export function AgentPanel({
  conversation,
  events,
  running,
  pendingFiles,
  sendError,
  width,
  onFilesChange,
  onSend,
  onApprove,
  onStop,
  onOpenImage,
  onOpenVersion,
  onDeleteMessage,
  onRegenerate,
}: AgentPanelProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const element = scrollRef.current;
    if (element) element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
  }, [events, running]);

  return (
    // The stylesheet keeps narrow-screen defaults, so the inline width only applies once the user drags.
    <aside
      className="ide-agent"
      aria-label={t("workspace.agent")}
      style={width !== DEFAULT_AGENT_WIDTH ? { width } : undefined}
    >
      <div className="ide-panel-header">
        <IconSparkles size={13} />
        {t("workspace.agent")}
      </div>
      <div className="ide-agent-scroll" ref={scrollRef}>
        {events.length === 0 ? (
          <div className="agent-welcome">
            <img alt="" className="h-10 w-10 opacity-80" src="/logo.png" />
            <p className="text-sm font-semibold text-[var(--ide-text)]">{t("agent.welcomeTitle")}</p>
            <p className="text-xs leading-relaxed">{t("agent.welcomeBody")}</p>
          </div>
        ) : (
          <ChatTimeline
            conversation={conversation}
            events={events}
            onApprove={onApprove}
            onDeleteMessage={onDeleteMessage}
            onOpenImage={onOpenImage}
            onOpenVersion={onOpenVersion}
            onRegenerate={onRegenerate}
            running={running}
          />
        )}
      </div>
      <div className="ide-agent-footer">
        {sendError ? <p className="agent-notice agent-notice-error mb-2">{sendError}</p> : null}
        <Composer onFilesChange={onFilesChange} onSend={onSend} onStop={onStop} pendingFiles={pendingFiles} running={running} />
      </div>
    </aside>
  );
}
