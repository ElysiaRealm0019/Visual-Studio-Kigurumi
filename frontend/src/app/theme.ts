import { createTheme, type Theme } from "@mui/material/styles";

export type ThemeMode = "dark" | "light";

export const THEME_STORAGE_KEY = "kigcraft.theme.v1";

/** Palette shared with the CSS variables in styles/index.css (keep both in sync). */
export const idePalettes = {
  dark: {
    accent: "#2e95f8",
    accentHover: "#52a8fa",
    accentContrast: "#04122a",
    background: "#0a1426",
    panel: "#0e1a30",
    panelRaised: "#14223b",
    border: "#1e2d48",
    borderStrong: "#2b3d5e",
    text: "#e3eaf5",
    textMuted: "#8796b0",
    hover: "#172741",
    input: "#071022",
    danger: "#f26d6d",
    success: "#43c38c",
    warning: "#e5b454",
  },
  light: {
    accent: "#116ced",
    accentHover: "#0b59c8",
    accentContrast: "#ffffff",
    background: "#f4f7fc",
    panel: "#ffffff",
    panelRaised: "#edf2f9",
    border: "#d9e2ef",
    borderStrong: "#c1cee1",
    text: "#0f1f3d",
    textMuted: "#5a6a86",
    hover: "#e8eff9",
    input: "#ffffff",
    danger: "#d64545",
    success: "#25966a",
    warning: "#b7791f",
  },
} as const;

export const uiFontFamily =
  '"Inter", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", system-ui, sans-serif';

export function readStoredThemeMode(): ThemeMode {
  if (typeof window === "undefined") return "dark";
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  return stored === "light" ? "light" : "dark";
}

export function createKigTheme(mode: ThemeMode): Theme {
  const p = idePalettes[mode];
  return createTheme({
    palette: {
      mode,
      primary: { main: p.accent, dark: p.accentHover, light: p.accentHover, contrastText: p.accentContrast },
      secondary: { main: p.textMuted, contrastText: p.background },
      error: { main: p.danger },
      warning: { main: p.warning },
      success: { main: p.success },
      background: { default: p.background, paper: p.panel },
      text: { primary: p.text, secondary: p.textMuted },
      divider: p.border,
    },
    shape: { borderRadius: 6 },
    typography: {
      fontFamily: uiFontFamily,
      fontSize: 13,
      button: { fontWeight: 600, letterSpacing: 0, textTransform: "none" },
      h1: { fontSize: "1.6rem", fontWeight: 700 },
      h2: { fontSize: "1.35rem", fontWeight: 700 },
      h3: { fontSize: "1.1rem", fontWeight: 650 },
      h4: { fontSize: "0.95rem", fontWeight: 650 },
      h5: { fontSize: "0.875rem", fontWeight: 650 },
    },
    components: {
      MuiButton: {
        defaultProps: { disableElevation: true, size: "small" },
        styleOverrides: { root: { borderRadius: 6, minWidth: 0, whiteSpace: "nowrap" } },
      },
      MuiIconButton: {
        styleOverrides: { root: { borderRadius: 6, color: p.textMuted, "&:hover": { backgroundColor: p.hover, color: p.text } } },
      },
      MuiPaper: { styleOverrides: { root: { backgroundImage: "none" } } },
      MuiMenu: {
        styleOverrides: {
          paper: { backgroundColor: p.panelRaised, border: `1px solid ${p.border}`, boxShadow: "0 8px 24px rgba(0,0,0,0.28)" },
        },
      },
      MuiSlider: {
        styleOverrides: {
          root: { color: p.accent, height: 3, padding: "10px 0" },
          rail: { backgroundColor: p.borderStrong, opacity: 1 },
          thumb: {
            backgroundColor: p.panel,
            border: `2px solid ${p.accent}`,
            height: 12,
            width: 12,
            boxShadow: "none",
            "&:hover, &.Mui-focusVisible": { boxShadow: `0 0 0 5px ${p.accent}33` },
          },
          track: { border: 0 },
        },
      },
      MuiTooltip: {
        styleOverrides: {
          tooltip: { backgroundColor: p.panelRaised, border: `1px solid ${p.border}`, color: p.text, fontSize: 12 },
        },
      },
      MuiAlert: {
        styleOverrides: { root: { border: `1px solid ${p.border}`, backgroundColor: p.panelRaised, color: p.text } },
      },
      MuiLinearProgress: {
        styleOverrides: { root: { backgroundColor: p.border, borderRadius: 2 }, bar: { backgroundColor: p.accent } },
      },
      MuiTab: { styleOverrides: { root: { minHeight: 36, textTransform: "none" } } },
      MuiTextField: {
        styleOverrides: {
          root: {
            "& .MuiFilledInput-root": {
              backgroundColor: p.input,
              border: `1px solid ${p.border}`,
              borderRadius: 6,
              "&:before, &:after": { display: "none" },
              "&:hover": { backgroundColor: p.input, borderColor: p.borderStrong },
              "&.Mui-focused": { backgroundColor: p.input, borderColor: p.accent },
            },
          },
        },
      },
    },
  });
}

/** Light theme kept as the default export for tests and legacy imports. */
export const kigTheme = createKigTheme("dark");
