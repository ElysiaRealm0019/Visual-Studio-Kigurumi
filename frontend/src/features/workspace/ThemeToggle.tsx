import { IconMoon, IconSun } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import { useThemeMode } from "../../app/providers";

export function ThemeToggle() {
  const { t } = useTranslation();
  const { mode, toggle } = useThemeMode();
  return (
    <button aria-label={t("workspace.toggleTheme")} className="ide-icon-button" onClick={toggle} title={t("workspace.toggleTheme")} type="button">
      {mode === "dark" ? <IconSun size={16} /> : <IconMoon size={16} />}
    </button>
  );
}
