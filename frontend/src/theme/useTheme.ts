import { useCallback, useState } from "react";

export type ThemeMode = "light" | "dark";

/** Keep in sync with public/theme-init.js, which applies it before the first paint. */
const STORAGE_KEY = "rutaviva.theme";

function currentMode(): ThemeMode {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export function useTheme() {
  const [mode, setMode] = useState<ThemeMode>(currentMode);

  const toggle = useCallback(() => {
    const next: ThemeMode = currentMode() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setMode(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage blocked: the choice lasts this visit.
    }
  }, []);

  return { mode, toggle };
}
