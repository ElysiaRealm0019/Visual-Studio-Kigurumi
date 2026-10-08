import { createTheme, type Theme } from "@mui/material/styles";

export type ThemeMode = "dark" | "light";

export const THEME_STORAGE_KEY = "kigcraft.theme.v1";

/** Palette shared with the CSS variables in styles/index.css (keep both in sync). */
export const idePalettes = {
  dark: {
    accent: "#7c7bf7",
    accentHover: "#908ffa",
    accentContrast: "#ffffff",
    background: "#16171b",
    panel: "#1c1d22",
    panelRaised: "#23242a",
    border: "#2d2f36",
    borderStrong: "#3b3e47",
    text: "#e4e5e9",
    textMuted: "#8b8f9a",
    hover: "#2a2c33",
    input: "#121317",
    danger: "#f26d6d",
    success: "#4cc38a",
    warning: "#e5b454",
  },
  light: {
    accent: "#5b5bd6",
    accentHover: "#4a4ac4",
    accentContrast: "#ffffff",
    background: "#f6f7f9",
    panel: "#ffffff",
    panelRaised: "#f1f2f5",
    border: "#e2e4e9",
    borderStrong: "#cdd0d7",
    text: "#1f2329",
    textMuted: "#6b7280",
    hover: "#eceef2",
    input: "#ffffff",
    danger: "#d64545",
    success: "#2f9e6a",
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
