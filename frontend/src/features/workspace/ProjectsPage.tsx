import { IconDots, IconFolderPlus, IconPencil, IconPhoto, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { normalizeLocale } from "../../i18n/locales";
import {
  createConversation,
  deleteConversation,
  listConversations,
  renameConversation,
  type ConversationSummary,
} from "../agent/agentApi";
import { LanguageSelector } from "../i18n/LanguageSelector";
import { ThemeToggle } from "./ThemeToggle";

export function ProjectsPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: listConversations });
  const [creating, setCreating] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);

  async function create(name: string) {
    const project = await createConversation(normalizeLocale(i18n.language), name);
    await queryClient.invalidateQueries({ queryKey: ["projects"] });
    navigate(`/p/${project.id}`);
  }

  async function remove(project: ConversationSummary) {
    setMenuFor(null);
    if (!window.confirm(t("workspace.confirmDelete"))) return;
    await deleteConversation(project.id);
    await queryClient.invalidateQueries({ queryKey: ["projects"] });
  }

  async function rename(project: ConversationSummary) {
    setMenuFor(null);
    const name = window.prompt(t("workspace.rename"), project.title || "");
    if (!name?.trim()) return;
    await renameConversation(project.id, name.trim());
    await queryClient.invalidateQueries({ queryKey: ["projects"] });
  }

  return (
    <div className="ide-home">
      <header className="ide-titlebar" style={{ height: 44 }}>
        <img alt="KigCraft" className="h-6 w-6" src="/logo.png" />
        <span className="text-sm font-semibold">KigCraft</span>
        <span className="flex-1" />
        <ThemeToggle />
        <LanguageSelector compact />
      </header>

      <main className="ide-home-main">
        <div className="mb-6 flex items-center gap-3">
          <h1 className="text-xl font-semibold">{t("workspace.projects")}</h1>
          <span className="flex-1" />
          <button className="ide-button ide-button-primary" onClick={() => setCreating(true)} type="button">
            <IconFolderPlus size={15} />
            {t("workspace.newProject")}
          </button>
        </div>

        {projects.data?.length === 0 ? (
          <div className="ide-welcome" style={{ padding: "12vh 0" }}>
            <img alt="" className="h-14 w-14 opacity-80" src="/logo.png" />
            <p className="max-w-md">{t("workspace.emptyProjects")}</p>
            <button className="ide-button ide-button-primary" onClick={() => setCreating(true)} type="button">
              <IconFolderPlus size={15} />
              {t("workspace.newProject")}
            </button>
          </div>
        ) : (
          <div className="ide-project-grid">
            {projects.data?.map((project) => (
              <div className="ide-project-card" key={project.id}>
                <button className="block w-full text-left" onClick={() => navigate(`/p/${project.id}`)} type="button">
                  <div className="ide-project-thumb">
                    {project.thumbnail_url ? <img alt="" src={project.thumbnail_url} /> : <IconPhoto size={28} />}
                  </div>
                  <div className="ide-project-meta">
                    <div className="truncate text-sm font-semibold">{project.title || t("workspace.untitled")}</div>
                    <div className="mt-1 flex items-center gap-2 text-xs text-[var(--ide-text-muted)]">
                      <span>{t("workspace.versionCount", { count: project.version_count })}</span>
                      <span>·</span>
                      <span>{new Date(project.created_at).toLocaleDateString(i18n.language)}</span>
                      {project.status === "running" ? <span className="ide-badge" data-tone="accent">●</span> : null}
                    </div>
                  </div>
                </button>
                <div className="ide-project-menu">
                  <button
                    aria-label={t("workspace.rename")}
                    className="ide-icon-button"
                    onClick={() => setMenuFor(menuFor === project.id ? null : project.id)}
                    type="button"
                  >
                    <IconDots size={16} />
                  </button>
                  {menuFor === project.id ? (
                    <div className="absolute right-0 top-8 z-10 flex w-36 flex-col rounded-md border border-[var(--ide-border)] bg-[var(--ide-panel-raised)] p-1 shadow-lg">
                      <button className="ide-tree-item" onClick={() => void rename(project)} type="button">
                        <IconPencil size={14} />
                        {t("workspace.rename")}
                      </button>
                      <button className="ide-tree-item text-[var(--ide-danger)]" onClick={() => void remove(project)} type="button">
                        <IconTrash size={14} />
                        {t("workspace.deleteProject")}
                      </button>
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </main>

      {creating ? <CreateProjectDialog onCancel={() => setCreating(false)} onCreate={create} /> : null}
    </div>
  );
}

function CreateProjectDialog({ onCancel, onCreate }: { onCancel: () => void; onCreate: (name: string) => Promise<void> }) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await onCreate(name.trim());
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ide-dialog-backdrop" onClick={onCancel} role="presentation">
      <form className="ide-dialog" onClick={(event) => event.stopPropagation()} onSubmit={(event) => void submit(event)}>
        <h2 className="text-base font-semibold">{t("workspace.newProject")}</h2>
        <input
          autoFocus
          className="ide-input"
          maxLength={80}
          onChange={(event) => setName(event.target.value)}
          placeholder={t("workspace.projectNamePlaceholder")}
          value={name}
        />
        <div className="flex justify-end gap-2">
          <button className="ide-button" onClick={onCancel} type="button">
            {t("workspace.cancel")}
          </button>
          <button className="ide-button ide-button-primary" disabled={busy} type="submit">
            {t("workspace.create")}
          </button>
        </div>
      </form>
    </div>
  );
}
