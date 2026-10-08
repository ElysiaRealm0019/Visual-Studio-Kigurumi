import { CssBaseline } from "@mui/material";
import { ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import "../i18n";
import { createKigTheme, readStoredThemeMode, THEME_STORAGE_KEY, type ThemeMode } from "./theme";

const queryClient = new QueryClient();

type ThemeModeContextValue = { mode: ThemeMode; toggle: () => void };

const ThemeModeContext = createContext<ThemeModeContextValue>({ mode: "dark", toggle: () => undefined });

export function useThemeMode() {
  return useContext(ThemeModeContext);
}

export function Providers({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(readStoredThemeMode);
  const theme = useMemo(() => createKigTheme(mode), [mode]);

  useEffect(() => {
    document.documentElement.dataset.theme = mode;
    window.localStorage.setItem(THEME_STORAGE_KEY, mode);
  }, [mode]);

  const value = useMemo(
    () => ({ mode, toggle: () => setMode((current) => (current === "dark" ? "light" : "dark")) }),
    [mode],
  );

  return (
    <ThemeModeContext.Provider value={value}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </ThemeProvider>
    </ThemeModeContext.Provider>
  );
}
