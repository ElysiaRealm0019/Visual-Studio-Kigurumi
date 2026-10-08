import { IconArrowLeft } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { LanguageSelector } from "../i18n/LanguageSelector";
import { ThemeToggle } from "../workspace/ThemeToggle";
import {
  backendRequirement,
  getBackendSettings,
  resetBackendSettings,
  updateBackendSettings,
  type BackendSettings,
} from "./settingsApi";

const settingsQueryKey = ["backend-settings"];

type Notice = { tone: "success" | "error"; text: string } | null;

export function SettingsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: settingsQueryKey, queryFn: getBackendSettings });
  const [form, setForm] = useState<Record<string, string>>({});
  // New API keys typed by the user; null clears the key saved on this page.
  const [secrets, setSecrets] = useState<Record<string, string | null>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  useEffect(() => {
    if (settings.data) {
      setForm(settings.data.values);
      setSecrets({});
    }
  }, [settings.data]);

  const data = settings.data;
  const changed = data ? Object.keys(form).filter((key) => form[key] !== data.values[key]) : [];
  const changedSecrets = Object.entries(secrets).filter(([, value]) => value === null || value.trim() !== "");
  const disabled = busy || !data?.writable;

  async function apply(action: () => Promise<BackendSettings>) {
    setBusy(true);
    setNotice(null);
    try {
      queryClient.setQueryData(settingsQueryKey, await action());
      setNotice({ tone: "success", text: t("settings.saved") });
    } catch (error) {
      setNotice({ tone: "error", text: t("settings.saveFailed", { error: error instanceof Error ? error.message : String(error) }) });
    } finally {
      setBusy(false);
    }
  }

  function save() {
    void apply(() =>
      updateBackendSettings({
        ...Object.fromEntries(changed.map((key) => [key, form[key].trim()])),
        ...Object.fromEntries(changedSecrets.map(([key, value]) => [key, value === null ? null : value.trim()])),
      }),
    );
  }

  function reset() {
    if (window.confirm(t("settings.confirmReset"))) void apply(resetBackendSettings);
  }

  const set = (key: string) => (value: string) => setForm((current) => ({ ...current, [key]: value }));
  const secretField = (key: string, hint?: string) =>
    data ? (
      <SecretField
        disabled={disabled}
        hint={hint}
        onChange={(value) => setSecrets((current) => ({ ...current, [key]: value }))}
        pending={secrets[key]}
        saved={data.secrets[key]}
      />
    ) : null;

  return (
    <div className="ide-home">
      <header className="ide-titlebar" style={{ height: 44 }}>
        <Link aria-label={t("workspace.home")} className="ide-icon-button" title={t("workspace.home")} to="/">
          <IconArrowLeft size={16} />
        </Link>
        <img alt="Visual Studio Kigurumi" className="h-6 w-6" src="/logo.png" />
        <span className="text-sm font-semibold">{t("settings.title")}</span>
        <span className="flex-1" />
        <ThemeToggle />
        <LanguageSelector compact />
      </header>
      <main className="ide-home-main" style={{ maxWidth: 760 }}>
        <h1 className="mb-2 text-xl font-semibold">{t("settings.title")}</h1>
        <p className="mb-1 text-sm text-[var(--ide-text-muted)]">{t("settings.intro")}</p>
        <p className="mb-6 text-xs text-[var(--ide-text-faint)]">{t("settings.keyStorageNote")}</p>
        {settings.isError ? <p className="text-sm text-[var(--ide-danger)]">{t("settings.loadFailed")}</p> : null}
        {data && !data.writable ? <p className="mb-4 text-sm text-[var(--ide-warning)]">{t("settings.readOnly")}</p> : null}
        {data ? (
          <div className="flex flex-col gap-4">
            <BackendSection
              data={data}
              disabled={disabled}
              hint={t("settings.agentHint")}
              onChange={set("agent_llm_provider")}
              settingKey="agent_llm_provider"
              title={t("settings.agentSection")}
              value={form.agent_llm_provider}
            >
              {form.agent_llm_provider === "claude_code" ? (
                <TextField disabled={disabled} label={t("settings.model")} onChange={set("agent_claude_code_model")} placeholder={t("settings.modelDefault")} value={form.agent_claude_code_model} />
              ) : (
                <>
                  <TextField disabled={disabled} label={t("settings.baseUrl")} onChange={set("agent_llm_base_url")} value={form.agent_llm_base_url} />
                  <TextField disabled={disabled} label={t("settings.model")} onChange={set("agent_llm_model")} value={form.agent_llm_model} />
                  {secretField("agent_llm_api_key", t("settings.agentKeyHint"))}
                </>
              )}
            </BackendSection>
            <BackendSection
              data={data}
              disabled={disabled}
              hint={t("settings.analysisHint")}
              onChange={set("llm_provider")}
              settingKey="llm_provider"
              title={t("settings.analysisSection")}
              value={form.llm_provider}
            >
              {form.llm_provider === "openai_compatible" ? (
                <>
                  <TextField
                    disabled={disabled}
                    label={t("settings.baseUrl")}
                    onChange={set("analysis_llm_base_url")}
                    placeholder={t("settings.sameAsAgent", { value: form.agent_llm_base_url })}
                    value={form.analysis_llm_base_url}
                  />
                  <TextField
                    disabled={disabled}
                    label={t("settings.model")}
                    onChange={set("analysis_llm_model")}
                    placeholder={t("settings.sameAsAgent", { value: form.agent_llm_model })}
                    value={form.analysis_llm_model}
                  />
                  {secretField("analysis_llm_api_key", t("settings.analysisKeyHint"))}
                  <p className="text-xs text-[var(--ide-text-muted)]">{t("settings.visionRequired")}</p>
                </>
              ) : null}
            </BackendSection>
            <BackendSection
              data={data}
              disabled={disabled}
              hint={t("settings.imageHint")}
              onChange={set("image_provider")}
              settingKey="image_provider"
              title={t("settings.imageSection")}
              value={form.image_provider}
            >
              {form.image_provider === "codex_bridge" ? (
                <>
                  <TextField disabled={disabled} label={t("settings.bridgeUrl")} onChange={set("codex_bridge_url")} value={form.codex_bridge_url} />
                  {secretField("codex_bridge_token")}
                </>
              ) : null}
              {form.image_provider === "siliconflow" ? (
                <>
                  <TextField disabled={disabled} label={t("settings.baseUrl")} onChange={set("siliconflow_base_url")} value={form.siliconflow_base_url} />
                  <TextField disabled={disabled} label={t("settings.model")} onChange={set("siliconflow_image_model")} value={form.siliconflow_image_model} />
                  {secretField("siliconflow_api_key")}
                </>
              ) : null}
              {form.image_provider === "ark" ? (
                <>
                  <TextField disabled={disabled} label={t("settings.baseUrl")} onChange={set("ark_base_url")} value={form.ark_base_url} />
                  <TextField disabled={disabled} label={t("settings.model")} onChange={set("ark_image_model")} value={form.ark_image_model} />
                  {secretField("ark_api_key")}
                </>
              ) : null}
            </BackendSection>
            <div className="flex items-center gap-3">
              <button className="ide-button ide-button-primary" disabled={disabled || changed.length + changedSecrets.length === 0} onClick={save} type="button">
                {t("settings.save")}
              </button>
              <button className="ide-button" disabled={disabled || data.overridden.length === 0} onClick={reset} type="button">
                {t("settings.reset")}
              </button>
              {notice ? (
                <span className="text-sm" role="status" style={{ color: notice.tone === "error" ? "var(--ide-danger)" : "var(--ide-success)" }}>
                  {notice.text}
                </span>
              ) : null}
            </div>
          </div>
        ) : null}
      </main>
    </div>
  );
}

function BackendSection({
  title,
  hint,
  settingKey,
  value,
  data,
  disabled,
  onChange,
  children,
}: {
  title: string;
  hint: string;
  settingKey: string;
  value: string | undefined;
  data: BackendSettings;
  disabled: boolean;
  onChange: (value: string) => void;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const options = data.options[settingKey] ?? [];
  const selectedRequirement = value ? backendRequirement(settingKey, value) : undefined;
  return (
    <section className="rounded-md border border-[var(--ide-border)] bg-[var(--ide-panel)] p-4">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        {data.overridden.includes(settingKey) ? <span className="ide-badge" data-tone="accent">{t("settings.overridden")}</span> : null}
      </div>
      <p className="mb-3 mt-1 text-xs text-[var(--ide-text-muted)]">{hint}</p>
      <div className="flex flex-col gap-1" role="radiogroup" aria-label={title}>
        {options.map((option) => {
          const requirement = backendRequirement(settingKey, option);
          const ready = !requirement || data.status[requirement] !== false;
          const label = t(`settings.providers.${option}`, { defaultValue: option });
          return (
            <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-[var(--ide-hover)]" key={option}>
              <input aria-label={label} checked={value === option} disabled={disabled} name={settingKey} onChange={() => onChange(option)} type="radio" value={option} />
              <span className="flex-1">{label}</span>
              {requirement ? (
                <span className="ide-badge" data-tone={ready ? "success" : undefined} title={ready ? undefined : t(`settings.status.${requirement}`)}>
                  {ready ? t("settings.ready") : t("settings.missing")}
                </span>
              ) : null}
            </label>
          );
        })}
      </div>
      {selectedRequirement && data.status[selectedRequirement] === false ? (
        <p className="mt-2 text-xs text-[var(--ide-danger)]">{t(`settings.status.${selectedRequirement}`)}</p>
      ) : null}
      {children ? <div className="mt-3 flex flex-col gap-3">{children}</div> : null}
    </section>
  );
}

function TextField({
  label,
  value,
  placeholder,
  disabled,
  onChange,
}: {
  label: string;
  value: string | undefined;
  placeholder?: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-[var(--ide-text-muted)]">
      {label}
      <input className="ide-input text-sm" disabled={disabled} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} value={value ?? ""} />
    </label>
  );
}

function SecretField({
  saved,
  pending,
  hint,
  disabled,
  onChange,
}: {
  saved: BackendSettings["secrets"][string] | undefined;
  pending: string | null | undefined;
  hint?: string;
  disabled: boolean;
  onChange: (value: string | null) => void;
}) {
  const { t } = useTranslation();
  const clearing = pending === null;
  const state = clearing
    ? t("settings.keyClearing")
    : saved?.set
      ? t(saved.source === "override" ? "settings.keyFromPage" : "settings.keyFromEnv", { hint: saved.hint })
      : t("settings.keyMissing");
  return (
    <div className="flex flex-col gap-1 text-xs text-[var(--ide-text-muted)]">
      <label className="flex flex-col gap-1">
        <span>
          {t("settings.apiKey")} · {state}
        </span>
        <input
          autoComplete="off"
          className="ide-input text-sm"
          disabled={disabled || clearing}
          onChange={(event) => onChange(event.target.value)}
          placeholder={saved?.set ? t("settings.keyReplace") : t("settings.keyEnter")}
          spellCheck={false}
          type="password"
          value={pending ?? ""}
        />
      </label>
      {hint ? <span>{hint}</span> : null}
      {saved?.source === "override" || clearing ? (
        <button
          className="self-start text-[var(--ide-accent)] hover:underline disabled:opacity-50"
          disabled={disabled}
          onClick={() => onChange(clearing ? "" : null)}
          type="button"
        >
          {clearing ? t("settings.keyUndoClear") : t("settings.keyClear")}
        </button>
      ) : null}
    </div>
  );
}
