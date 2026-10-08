import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

/**
 * In-app replacements for window.confirm / window.prompt. Native dialogs are silently suppressed in some
 * embedded browsers and webviews, which made actions behind them look broken.
 *
 * `onConfirm` may throw; the message is shown in the dialog and it stays open so the user can retry or cancel.
 */
type DialogBaseProps = {
  title: string;
  confirmLabel: string;
  danger?: boolean;
  onCancel: () => void;
  errorMessage?: (error: unknown) => string;
};

export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  danger,
  onCancel,
  onConfirm,
  errorMessage,
}: DialogBaseProps & { message: string; onConfirm: () => Promise<void> | void }) {
  return (
    <DialogShell
      confirmLabel={confirmLabel}
      danger={danger}
      errorMessage={errorMessage}
      onCancel={onCancel}
      onSubmit={onConfirm}
      title={title}
    >
      <p className="text-sm text-[var(--ide-text-muted)]">{message}</p>
    </DialogShell>
  );
}

export function PromptDialog({
  title,
  initialValue = "",
  placeholder,
  maxLength,
  confirmLabel,
  onCancel,
  onConfirm,
  errorMessage,
}: DialogBaseProps & {
  initialValue?: string;
  placeholder?: string;
  maxLength?: number;
  onConfirm: (value: string) => Promise<void> | void;
}) {
  const [value, setValue] = useState(initialValue);
  return (
    <DialogShell
      confirmLabel={confirmLabel}
      disabled={!value.trim()}
      errorMessage={errorMessage}
      onCancel={onCancel}
      onSubmit={() => onConfirm(value.trim())}
      title={title}
    >
      <input
        aria-label={title}
        autoFocus
        className="ide-input"
        maxLength={maxLength}
        onChange={(event) => setValue(event.target.value)}
        onFocus={(event) => event.target.select()}
        placeholder={placeholder}
        value={value}
      />
    </DialogShell>
  );
}

function DialogShell({
  title,
  confirmLabel,
  danger,
  disabled,
  onCancel,
  onSubmit,
  errorMessage,
  children,
}: DialogBaseProps & { disabled?: boolean; onSubmit: () => Promise<void> | void; children: ReactNode }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || disabled) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit();
    } catch (caught) {
      setError(errorMessage ? errorMessage(caught) : caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ide-dialog-backdrop" onClick={busy ? undefined : onCancel} role="presentation">
      <form
        aria-label={title}
        className="ide-dialog"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === "Escape" && !busy) onCancel();
        }}
        onSubmit={(event) => void submit(event)}
        role="dialog"
      >
        <h2 className="text-base font-semibold">{title}</h2>
        {children}
        {error ? (
          <p className="text-sm text-[var(--ide-danger)]" role="alert">
            {error}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <button className="ide-button" disabled={busy} onClick={onCancel} type="button">
            {t("workspace.cancel")}
          </button>
          <button className={`ide-button ${danger ? "ide-button-danger" : "ide-button-primary"}`} disabled={busy || disabled} type="submit">
            {confirmLabel}
          </button>
        </div>
      </form>
    </div>
  );
}
