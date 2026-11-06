import { IconCube } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { blenderText } from "./blenderText";

export function BlenderLink() {
  const { i18n } = useTranslation();
  const label = blenderText(i18n.language).open;
  return (
    <Link aria-label={label} className="ide-icon-button" title={label} to="/blender">
      <IconCube size={16} />
    </Link>
  );
}
