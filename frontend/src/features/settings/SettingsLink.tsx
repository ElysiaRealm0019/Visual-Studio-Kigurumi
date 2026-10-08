import { IconSettings } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

export function SettingsLink() {
  const { t } = useTranslation();
  return (
    <Link aria-label={t("settings.open")} className="ide-icon-button" title={t("settings.open")} to="/settings">
      <IconSettings size={16} />
    </Link>
  );
}
