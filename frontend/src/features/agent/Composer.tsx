import { IconArrowUp, IconPhotoPlus, IconPlayerStopFilled, IconX } from "@tabler/icons-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

const MAX_FILES = 6;

type ComposerProps = {
  running: boolean;
  disabled?: boolean;
  pendingFiles: File[];
  onFilesChange: (files: File[]) => void;
  onSend: (text: string, files: File[]) => Promise<boolean>;
  onStop: () => void;
};

export function Composer({ running, disabled = false, pendingFiles, onFilesChange, onSend, onStop }: ComposerProps) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const previews = useMemo(() => pendingFiles.map((file) => URL.createObjectURL(file)), [pendingFiles]);

  useEffect(() => () => previews.forEach((url) => URL.revokeObjectURL(url)), [previews]);

  useEffect(() => fitTextarea(textareaRef.current), [text]);

  useEffect(() => {
    // The first measurement can happen before the flex layout gives the textarea its width.
    const textarea = textareaRef.current;
    if (!textarea || typeof ResizeObserver === "undefined") return;
    let lastWidth = 0;
    const observer = new ResizeObserver(([entry]) => {
      if (Math.abs(entry.contentRect.width - lastWidth) < 1) return;
      lastWidth = entry.contentRect.width;
      fitTextarea(textarea);
    });
    observer.observe(textarea);
    return () => observer.disconnect();
  }, []);

  const canSend = !running && !sending && !disabled && (text.trim().length > 0 || pendingFiles.length > 0);

  async function submit() {
    if (!canSend) return;
    setSending(true);
    const ok = await onSend(text.trim(), pendingFiles);
    setSending(false);
    if (ok) {
      setText("");
      onFilesChange([]);
      textareaRef.current?.focus();
    }
  }

  function addFiles(files: Iterable<File>) {
    const images = [...files].filter((file) => file.type.startsWith("image/"));
    if (images.length) onFilesChange([...pendingFiles, ...images].slice(0, MAX_FILES));
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  }

  return (
    <div className="agent-composer">
      {pendingFiles.length ? (
        <div className="flex flex-wrap gap-2 px-3 pt-3">
          {pendingFiles.map((file, index) => (
            <div className="agent-thumb" key={`${file.name}-${index}`}>
              <img alt={file.name} src={previews[index]} />
              <button
                aria-label={t("common.delete")}
                className="agent-thumb-remove"
                onClick={() => onFilesChange(pendingFiles.filter((_, other) => other !== index))}
                type="button"
              >
                <IconX size={12} />
              </button>
            </div>
          ))}
        </div>
      ) : null}
      <div className="flex items-end gap-2 p-2">
        <button
          aria-label={t("agent.attach")}
          className="agent-icon-button"
          disabled={running || pendingFiles.length >= MAX_FILES}
          onClick={() => fileInputRef.current?.click()}
          title={t("agent.attach")}
          type="button"
        >
          <IconPhotoPlus size={20} />
        </button>
        <input
          accept="image/png,image/jpeg,image/webp"
          className="hidden"
          multiple
          onChange={(event) => {
            addFiles(event.target.files ?? []);
            event.target.value = "";
          }}
          ref={fileInputRef}
          type="file"
        />
        <textarea
          aria-label={t("agent.placeholder")}
          className="agent-textarea"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          onPaste={(event) => {
            const files = [...event.clipboardData.files];
            if (files.length) {
              event.preventDefault();
              addFiles(files);
            }
          }}
          placeholder={t("agent.placeholder")}
          ref={textareaRef}
          rows={1}
          value={text}
        />
        {running ? (
          <button aria-label={t("agent.stop")} className="agent-send" onClick={onStop} title={t("agent.stop")} type="button">
            <IconPlayerStopFilled size={18} />
          </button>
        ) : (
          <button
            aria-label={t("agent.send")}
            className="agent-send"
            disabled={!canSend}
            onClick={() => void submit()}
            title={t("agent.send")}
            type="button"
          >
            <IconArrowUp size={20} />
          </button>
        )}
      </div>
    </div>
  );
}

function fitTextarea(textarea: HTMLTextAreaElement | null) {
  if (!textarea) return;
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
}
