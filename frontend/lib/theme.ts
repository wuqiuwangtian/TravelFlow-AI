/** Browser-local appearance, independent of provider settings and travel data. */
export const THEME_STORAGE_KEY = "travelflow.theme";
export const THEME_CHANGED_EVENT = "travelflow:theme-changed";
export const THEMES = [
  {
    id: "paper",
    label: "A · 暖纸墨绿",
    description: "温暖纸面，沉静的墨绿强调",
    default: true,
  },
  {
    id: "guide",
    label: "B · 冷白深蓝",
    description: "清爽白底，清晰的深蓝强调",
    default: false,
  },
  {
    id: "night",
    label: "C · 深灰绿",
    description: "深色纸面，柔和的浅绿强调",
    default: false,
  },
] as const;
export type Theme = (typeof THEMES)[number]["id"];
export function validTheme(value: unknown): value is Theme {
  return value === "paper" || value === "guide" || value === "night";
}
export function currentTheme(): Theme {
  if (typeof document === "undefined") return "paper";
  const value = document.documentElement.dataset.theme;
  return validTheme(value) ? value : "paper";
}
export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  window.dispatchEvent(new Event(THEME_CHANGED_EVENT));
}
export function saveTheme(theme: Theme): boolean {
  applyTheme(theme);
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    return true;
  } catch {
    return false;
  }
}
export function subscribeTheme(callback: () => void) {
  window.addEventListener(THEME_CHANGED_EVENT, callback);
  return () => window.removeEventListener(THEME_CHANGED_EVENT, callback);
}
export const serverTheme = (): Theme => "paper";

// Fixed script executes in <head> before page content paints. Never interpolate stored text.
export const THEME_INIT_SCRIPT = `try{var t=localStorage.getItem("${THEME_STORAGE_KEY}");if(t==="paper"||t==="guide"||t==="night")document.documentElement.dataset.theme=t}catch{}`;
