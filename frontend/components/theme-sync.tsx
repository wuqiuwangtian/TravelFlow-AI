"use client";

import { useEffect } from "react";
import { applyTheme, THEME_STORAGE_KEY, validTheme } from "@/lib/theme";

/** Keep every route in step with other tabs, without replacing local form drafts. */
export function ThemeSync() {
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== THEME_STORAGE_KEY && event.key !== null) return;
      try {
        if (event.storageArea !== window.localStorage) return;
      } catch {
        return;
      }
      applyTheme(validTheme(event.newValue) ? event.newValue : "paper");
    };
    const onRestore = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      try {
        const value = window.localStorage.getItem(THEME_STORAGE_KEY);
        applyTheme(validTheme(value) ? value : "paper");
      } catch {
        /* Keep the current appearance if browser storage is unavailable. */
      }
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener("pageshow", onRestore);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("pageshow", onRestore);
    };
  }, []);
  return null;
}
