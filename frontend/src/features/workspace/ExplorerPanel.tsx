import { IconPlus } from "@tabler/icons-react";
import { useRef } from "react";
import { useTranslation } from "react-i18next";
import type { Conversation, DesignImage } from "../agent/agentApi";

type ExplorerPanelProps = {
  conversation: Conversation | null;
  activeVersionId: string | null;
  disabled: boolean;
  onOpenVersion: (imageId: string) => void;
  onOpenImage: (url: string) => void;
  onAddReferences: (files: File[]) => void;
};

export function ExplorerPanel({
  conversation,
  activeVersionId,
  disabled,
  onOpenVersion,
  onOpenImage,
  onAddReferences,
}: ExplorerPanelProps) {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const references = conversation?.state.references ?? [];
  const images = conversation?.state.images ?? [];
  const state = conversation?.state;
  const approvedIds = new Set([state?.approved_design_id, state?.approved_front_id].filter(Boolean) as string[]);
  const currentIds = new Set([state?.current_design_id, state?.current_front_id].filter(Boolean) as string[]);

  return (
    <>
      <div className="ide-sidebar-header">{t("workspace.explorer")}</div>
      <div className="ide-sidebar-scroll">
        <div className="ide-section-title">
          <span>{t("workspace.references")}</span>
          <span className="ide-spacer" />
          <button
            aria-label={t("workspace.addReference")}
            className="ide-icon-button"
            disabled={disabled}
            onClick={() => fileInputRef.current?.click()}
            title={t("workspace.addReference")}
            type="button"
          >
            <IconPlus size={14} />
          </button>
          <input
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            multiple
            onChange={(event) => {
              const files = [...(event.target.files ?? [])];
              event.target.value = "";
              if (files.length) onAddReferences(files);
            }}
            ref={fileInputRef}
            type="file"
          />
        </div>
        {references.length === 0 ? (
          <p className="ide-empty">{t("workspace.noReferences")}</p>
        ) : (
          <div className="ide-ref-grid">
            {references.map((reference) => (
              <button
                className="ide-ref-thumb"
                key={reference.id}
                onClick={() => onOpenImage(reference.url)}
                title={reference.file_name}
                type="button"
              >
                <img alt={reference.file_name} src={reference.url} />
                {reference.kind === "front" ? <span className="ide-ref-thumb-tag">{t("agent.referenceMain")}</span> : null}
              </button>
            ))}
          </div>
        )}

        <div className="ide-section-title" style={{ marginTop: 18 }}>
          <span>{t("workspace.versions")}</span>
        </div>
        {images.length === 0 ? (
          <p className="ide-empty">{t("workspace.noVersions")}</p>
        ) : (
          <VersionTree
            activeVersionId={activeVersionId}
            approvedIds={approvedIds}
            currentIds={currentIds}
            images={images}
            onOpenVersion={onOpenVersion}
          />
        )}
      </div>
    </>
  );
}

function VersionTree({
  images,
  activeVersionId,
  approvedIds,
  currentIds,
  onOpenVersion,
}: {
  images: DesignImage[];
  activeVersionId: string | null;
  approvedIds: Set<string>;
  currentIds: Set<string>;
  onOpenVersion: (imageId: string) => void;
}) {
  const { t } = useTranslation();
  const ids = new Set(images.map((image) => image.id));
  const children = new Map<string | null, DesignImage[]>();
  for (const image of images) {
    // Versions hang under the one they were made from (head shell under its design, turnaround under its
    // front); roots are versions without a known parent.
    const parent = image.parent_id && ids.has(image.parent_id) ? image.parent_id : null;
    children.set(parent, [...(children.get(parent) ?? []), image]);
  }

  function renderNode(image: DesignImage, depth: number) {
    const kids = children.get(image.id) ?? [];
    return (
      <li key={image.id}>
        <button
          className="ide-tree-item"
          data-active={image.id === activeVersionId}
          onClick={() => onOpenVersion(image.id)}
          style={{ paddingLeft: 6 + depth * 14 }}
          title={image.instructions || image.id}
          type="button"
        >
          <img alt="" src={image.url} />
          <span className="ide-tree-item-text">
            <span className="ide-tree-item-title">
              {image.id}
              <span className="ide-badge">{t(`workspace.sources.${image.source}`)}</span>
              {approvedIds.has(image.id) ? (
                <span className="ide-badge" data-tone="success">
                  {t("workspace.approved")}
                </span>
              ) : currentIds.has(image.id) ? (
                <span className="ide-badge" data-tone="accent">
                  {t("workspace.current")}
                </span>
              ) : null}
            </span>
            <span className="ide-tree-item-sub">
              {t(`workspace.roles.${image.role}`)} · {image.width}×{image.height}
              {image.instructions ? ` · ${image.instructions}` : ""}
            </span>
          </span>
        </button>
        {kids.length ? <ul>{kids.map((kid) => renderNode(kid, depth + 1))}</ul> : null}
      </li>
    );
  }

  return <ul className="flex flex-col gap-0.5">{(children.get(null) ?? []).map((image) => renderNode(image, 0))}</ul>;
}
