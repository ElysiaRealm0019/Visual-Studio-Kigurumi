import { IconArrowLeft, IconDownload, IconPlayerStop, IconRefresh, IconTool } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { LanguageSelector } from "../i18n/LanguageSelector";
import { ThemeToggle } from "../workspace/ThemeToggle";
import {
  TERMINAL_STATUSES,
  buildFileUrl,
  cancelBuild,
  createBuild,
  defaultParams,
  getBlenderStatus,
  getBuild,
  getShellSchema,
  listBuilds,
  validateParams,
  type BlenderJob,
  type ParamSpec,
  type ParamValue,
  type Params,
  type Validated,
} from "./blenderApi";
import { blenderText, formatValue, specLabel } from "./blenderText";

const VALIDATE_DELAY_MS = 250;
const POLL_MS = 1000;

export function BlenderPage() {
  const { i18n } = useTranslation();
  const lang = i18n.language;
  const text = blenderText(lang);
  const queryClient = useQueryClient();
  const schemaQuery = useQuery({ queryKey: ["blender-schema"], queryFn: getShellSchema });
  const statusQuery = useQuery({ queryKey: ["blender-status"], queryFn: getBlenderStatus });
  const historyQuery = useQuery({ queryKey: ["blender-jobs"], queryFn: listBuilds });
  const schema = schemaQuery.data?.schema;

  // Server-side defaults: POST /validate with empty params fills them in (sizing.validate), so the
  // frontend no longer owns a second copy of the default values.
  const defaultsQuery = useQuery({
    queryKey: ["blender-defaults", schema?.schema_version],
    queryFn: () => validateParams({}, schema?.schema_version ?? 0),
    enabled: schema !== undefined,
    staleTime: Infinity,
  });
  const serverDefaults = defaultsQuery.data?.params;

  const [params, setParams] = useState<Params | null>(null);
  const [validated, setValidated] = useState<Validated | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (params !== null) return;
    if (serverDefaults) setParams({ ...serverDefaults });
    else if (schema && defaultsQuery.isError) setParams(defaultParams(schema));
  }, [schema, params, serverDefaults, defaultsQuery.isError]);

  // Baseline for reset / preset / history: the server defaults when available, schema defaults otherwise.
  const baseline = useCallback((): Params => {
    if (serverDefaults) return { ...serverDefaults };
    return schema ? defaultParams(schema) : {};
  }, [serverDefaults, schema]);

  // live clamping + derived size from the backend (same code as the Blender build)
  useEffect(() => {
    if (!schema || !params) return;
    let stale = false;
    const timer = window.setTimeout(() => {
      validateParams(params, schema.schema_version)
        .then((result) => !stale && setValidated(result))
        .catch((caught: unknown) => !stale && setError(caught instanceof Error ? caught.message : String(caught)));
    }, VALIDATE_DELAY_MS);
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
  }, [schema, params]);

  const jobQuery = useQuery({
    queryKey: ["blender-job", jobId],
    queryFn: () => getBuild(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (query) => (query.state.data && TERMINAL_STATUSES.has(query.state.data.status) ? false : POLL_MS),
  });
  const job = jobQuery.data ?? null;
  const running = job !== null && !TERMINAL_STATUSES.has(job.status);

  useEffect(() => {
    if (job && TERMINAL_STATUSES.has(job.status)) void queryClient.invalidateQueries({ queryKey: ["blender-jobs"] });
  }, [job, queryClient]);

  const groups = useMemo(() => {
    if (!schema) return [];
    return schema.groups.map((group) => ({
      ...group,
      params: schema.params.filter((spec) => spec.group === group.key && !spec.readonly),
    }));
  }, [schema]);

  function set(key: string, value: ParamValue) {
    setParams((current) => (current ? { ...current, [key]: value } : current));
  }

  function applyPreset(id: string) {
    const preset = schemaQuery.data?.presets.find((item) => item.id === id);
    if (schema && preset) setParams({ ...baseline(), ...preset.params });
  }

  async function build() {
    if (!schema || !params) return;
    setError(null);
    try {
      const created = await createBuild(params, schema.schema_version);
      queryClient.setQueryData(["blender-job", created.id], created);
      setJobId(created.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function cancel() {
    if (!jobId) return;
    queryClient.setQueryData(["blender-job", jobId], await cancelBuild(jobId));
  }

  const available = statusQuery.data?.available ?? false;

  return (
    <div className="ide-home">
      <header className="ide-titlebar" style={{ height: 44 }}>
        <Link aria-label="home" className="ide-icon-button" title="home" to="/">
          <IconArrowLeft size={16} />
        </Link>
        <img alt="Visual Studio Kigurumi" className="h-6 w-6" src="/logo.png" />
        <span className="text-sm font-semibold">{text.title}</span>
        <span className="flex-1" />
        <ThemeToggle />
        <LanguageSelector compact />
      </header>
      <main className="ide-home-main" style={{ maxWidth: 1200 }}>
        <h1 className="mb-2 text-xl font-semibold">{text.title}</h1>
        <p className="mb-4 text-sm text-[var(--ide-text-muted)]">{text.intro}</p>
        {statusQuery.data && !available ? (
          <p className="mb-4 text-sm text-[var(--ide-warning)]" role="alert">
            {text.unavailable}
            {statusQuery.data.reasons.join("；")}
          </p>
        ) : null}
        {schemaQuery.isError ? <p className="text-sm text-[var(--ide-danger)]">{text.loadFailed}</p> : null}
        {schema && params ? (
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
            <div className="flex min-w-0 flex-1 flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <select
                  aria-label={text.preset}
                  className="ide-input text-sm"
                  onChange={(event) => {
                    applyPreset(event.target.value);
                    event.target.value = "";
                  }}
                  value=""
                >
                  <option value="">{text.presetPick}</option>
                  {schemaQuery.data?.presets.map((preset) => (
                    <option key={preset.id} value={preset.id}>
                      {preset.name}
                    </option>
                  ))}
                </select>
                <button className="ide-button" onClick={() => setParams(baseline())} type="button">
                  <IconRefresh size={14} />
                  {text.reset}
                </button>
              </div>
              {groups.map((group, index) => (
                <details
                  className="rounded-md border border-[var(--ide-border)] bg-[var(--ide-panel)] px-4 py-2"
                  key={group.key}
                  open={index < 4}
                >
                  <summary className="cursor-pointer py-1 text-sm font-semibold">{specLabel(group, lang)}</summary>
                  <div className="flex flex-col gap-3 py-2">
                    {group.params.map((spec) => (
                      <ParamControl
                        autoLabel={text.auto}
                        key={spec.key}
                        lang={lang}
                        onChange={(value) => set(spec.key, value)}
                        spec={spec}
                        value={params[spec.key] ?? null}
                      />
                    ))}
                  </div>
                </details>
              ))}
            </div>
            <aside className="flex w-full flex-col gap-3 lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:w-[420px] lg:overflow-y-auto">
              <Summary text={text} validated={validated} />
              <div className="flex items-center gap-2">
                <button
                  className="ide-button ide-button-primary"
                  disabled={!available || running || !validated}
                  onClick={() => void build()}
                  type="button"
                >
                  <IconTool size={14} />
                  {running ? text.building : text.build}
                </button>
                {running ? (
                  <button className="ide-button" onClick={() => void cancel()} type="button">
                    <IconPlayerStop size={14} />
                    {text.cancel}
                  </button>
                ) : null}
              </div>
              {error ? <p className="text-xs text-[var(--ide-danger)]">{error}</p> : null}
              {job ? <JobView job={job} text={text} /> : null}
              <History
                jobs={historyQuery.data ?? []}
                onLoad={(item) => setParams({ ...baseline(), ...item.params })}
                onOpen={(item) => setJobId(item.id)}
                text={text}
              />
            </aside>
          </div>
        ) : null}
      </main>
    </div>
  );
}

type Text = ReturnType<typeof blenderText>;

function ParamControl({
  spec,
  value,
  lang,
  autoLabel,
  onChange,
}: {
  spec: ParamSpec;
  value: ParamValue;
  lang: string;
  autoLabel: string;
  onChange: (value: ParamValue) => void;
}) {
  const label = specLabel(spec, lang);
  const unit = spec.unit ? ` (${spec.unit})` : "";
  const hint = spec.description ? <span className="text-[var(--ide-text-faint)]">{spec.description}</span> : null;
  const id = `param-${spec.key}`;

  if (spec.type === "float" || spec.type === "int") {
    const isNull = value === null;
    const numeric = typeof value === "number" ? value : (spec.min ?? 0);
    return (
      <div className="flex flex-col gap-1 text-xs text-[var(--ide-text-muted)]">
        <div className="flex items-center gap-2">
          <label className="min-w-0 flex-1" htmlFor={id}>
            {label}
            {unit}
          </label>
          {spec.nullable ? (
            <label className="flex shrink-0 items-center gap-1">
              <input
                checked={isNull}
                onChange={(event) => onChange(event.target.checked ? null : (typeof spec.default === "number" ? spec.default : numeric))}
                type="checkbox"
              />
              {autoLabel}
            </label>
          ) : null}
          <input
            aria-label={label}
            className="ide-input text-right text-sm"
            disabled={isNull}
            id={id}
            max={spec.max}
            min={spec.min}
            onChange={(event) => {
              const next = event.target.value === "" ? null : Number(event.target.value);
              onChange(next === null && !spec.nullable ? numeric : next);
            }}
            step={spec.step}
            style={{ width: 96, flex: "none" }}
            type="number"
            value={isNull ? "" : formatValue(spec, numeric)}
          />
        </div>
        {spec.min !== undefined && spec.max !== undefined ? (
          <input
            aria-hidden
            disabled={isNull}
            max={spec.max}
            min={spec.min}
            onChange={(event) => onChange(Number(event.target.value))}
            step={spec.step ?? (spec.type === "int" ? 1 : 0.01)}
            tabIndex={-1}
            type="range"
            value={numeric}
          />
        ) : null}
        {hint}
      </div>
    );
  }
  if (spec.type === "enum") {
    return (
      <label className="flex flex-col gap-1 text-xs text-[var(--ide-text-muted)]">
        {label}
        <select
          className="ide-input text-sm"
          onChange={(event) => {
            const option = spec.options?.find((item) => String(item) === event.target.value);
            onChange(option ?? null);
          }}
          value={String(value)}
        >
          {spec.options?.map((option) => (
            <option key={String(option)} value={String(option)}>
              {String(option)}
            </option>
          ))}
        </select>
        {hint}
      </label>
    );
  }
  if (spec.type === "bool") {
    return (
      <label className="flex items-center gap-2 text-xs text-[var(--ide-text-muted)]">
        <input checked={value === true} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
        {label}
      </label>
    );
  }
  if (spec.type === "multi") {
    const selected = Array.isArray(value) ? value : [];
    return (
      <fieldset className="flex flex-col gap-1 text-xs text-[var(--ide-text-muted)]">
        <legend>{label}</legend>
        <div className="flex flex-wrap gap-3">
          {spec.options?.map((option) => {
            const key = String(option);
            return (
              <label className="flex items-center gap-1" key={key}>
                <input
                  checked={selected.includes(key)}
                  onChange={(event) =>
                    onChange(event.target.checked ? [...selected, key] : selected.filter((item) => item !== key))
                  }
                  type="checkbox"
                />
                {key}
              </label>
            );
          })}
        </div>
        {hint}
      </fieldset>
    );
  }
  return (
    <label className="flex flex-col gap-1 text-xs text-[var(--ide-text-muted)]">
      {label}
      <input
        className="ide-input text-sm"
        maxLength={spec.max_length}
        onChange={(event) => onChange(event.target.value)}
        value={typeof value === "string" ? value : ""}
      />
      {hint}
    </label>
  );
}

function Summary({ validated, text }: { validated: Validated | null; text: Text }) {
  if (!validated) return null;
  const d = validated.derived;
  const mm = (values: number[]) => values.map((value) => value.toFixed(1)).join(" × ");
  const rows: [string, string][] = [
    [text.scale, `${d.s.toFixed(2)}（s_req ${d.s_req.toFixed(3)}）`],
    [text.required, mm(d.required_mm)],
    [text.actual, mm(d.actual_mm)],
    [text.recommended, d.recommended_tier],
    [text.tightest, text.axis[d.tightest_axis] ?? d.tightest_axis],
    [text.neck, `${d.neck.minor_mm.toFixed(1)} × ${d.neck.major_mm.toFixed(1)}`],
    [text.viewSpacing, `${d.view_spacing_mm.toFixed(1)}（${text.spacingSource[d.view_spacing_source] ?? d.view_spacing_source}）`],
  ];
  if (d.shoulder_ratio !== undefined) rows.push([text.shoulder, `1 : ${d.shoulder_ratio.toFixed(2)}`]);
  return (
    <section className="rounded-md border border-[var(--ide-border)] bg-[var(--ide-panel)] p-4" data-testid="blender-summary">
      <h2 className="mb-2 text-sm font-semibold">{text.summary}</h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        {rows.map(([key, value]) => (
          <div className="contents" key={key}>
            <dt className="text-[var(--ide-text-muted)]">{key}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {validated.warnings.length ? (
        <div className="mt-3 text-xs text-[var(--ide-warning)]" role="status">
          <div className="font-semibold">{text.warnings}</div>
          <ul className="list-disc pl-4">
            {validated.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function JobView({ job, text }: { job: BlenderJob; text: Text }) {
  const files = job.result?.files ?? [];
  const renders = files.filter((file) => file.kind === "render");
  const models = files.filter((file) => file.kind === "model");
  const measured = job.result?.measured;
  return (
    <section className="flex flex-col gap-2 rounded-md border border-[var(--ide-border)] bg-[var(--ide-panel)] p-4" data-testid="blender-job">
      <div className="flex items-center gap-2 text-xs">
        <span className="font-semibold">{job.phase_label}</span>
        <span className="flex-1" />
        <span>{job.progress}%</span>
      </div>
      <progress className="w-full" max={100} value={job.progress} />
      {job.status === "failed" ? <p className="text-xs text-[var(--ide-danger)]">{text.failed}：{job.error}</p> : null}
      {job.status === "failed" && job.log_tail.length ? (
        <details className="text-xs" data-testid="blender-job-log" open>
          <summary className="cursor-pointer text-[var(--ide-text-muted)]">{text.log}</summary>
          <pre className="mt-1 max-h-64 overflow-auto rounded border border-[var(--ide-border)] bg-[var(--ide-bg)] p-2">
            {job.log_tail.join("\n")}
          </pre>
        </details>
      ) : null}
      {job.status === "cancelled" ? <p className="text-xs text-[var(--ide-text-muted)]">{text.cancelled}</p> : null}
      {measured ? (
        <p className="text-xs text-[var(--ide-text-muted)]">
          {text.measured}：{measured.width_mm} × {measured.depth_mm} × {measured.height_mm} mm
        </p>
      ) : null}
      {renders.length ? (
        <div className="grid grid-cols-2 gap-2">
          {renders.map((file) => (
            <a href={buildFileUrl(job.id, file.name)} key={file.name} rel="noreferrer" target="_blank">
              <img alt={file.view ?? file.name} className="w-full rounded border border-[var(--ide-border)]" src={buildFileUrl(job.id, file.name)} />
            </a>
          ))}
        </div>
      ) : null}
      {models.length ? (
        <div className="flex flex-col gap-1 text-xs">
          <div className="font-semibold">{text.downloads}</div>
          {models.map((file) => (
            <a className="flex items-center gap-1 text-[var(--ide-accent)] hover:underline" download href={buildFileUrl(job.id, file.name)} key={file.name}>
              <IconDownload size={12} />
              {text.parts[file.part ?? ""] ?? file.part} · {file.format.toUpperCase()}
              {file.bytes ? ` · ${(file.bytes / 1_048_576).toFixed(1)} MB` : ""}
              {file.manifold === false ? <span className="text-[var(--ide-warning)]"> · {text.nonManifold}</span> : null}
            </a>
          ))}
        </div>
      ) : null}
      {TERMINAL_STATUSES.has(job.status) && job.warnings.length ? (
        <ul className="list-disc pl-4 text-xs text-[var(--ide-warning)]">
          {job.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function History({
  jobs,
  text,
  onOpen,
  onLoad,
}: {
  jobs: BlenderJob[];
  text: Text;
  onOpen: (job: BlenderJob) => void;
  onLoad: (job: BlenderJob) => void;
}) {
  return (
    <section className="rounded-md border border-[var(--ide-border)] bg-[var(--ide-panel)] p-4">
      <h2 className="mb-2 text-sm font-semibold">{text.history}</h2>
      {jobs.length === 0 ? <p className="text-xs text-[var(--ide-text-muted)]">{text.noHistory}</p> : null}
      <ul className="flex flex-col gap-1 text-xs">
        {jobs.map((job) => (
          <li className="flex items-center gap-2" key={job.id}>
            <button className="flex-1 truncate text-left hover:underline" onClick={() => onOpen(job)} type="button">
              {new Date(job.created_at).toLocaleString()} · s {typeof job.derived.s === "number" ? job.derived.s.toFixed(2) : "-"} · {job.phase_label}
            </button>
            <button className="text-[var(--ide-accent)] hover:underline" onClick={() => onLoad(job)} type="button">
              {text.loadParams}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
