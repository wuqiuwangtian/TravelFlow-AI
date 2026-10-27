import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function scrollToElement(
  element: Element | null,
  options: ScrollIntoViewOptions = {},
) {
  element?.scrollIntoView({
    ...options,
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "instant"
      : "smooth",
  });
}

/** Scroll message content without pulling a mobile route out of view. */
export function scrollToLatest(element: HTMLElement | null) {
  element?.scrollTo({
    top: element.scrollHeight,
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "instant"
      : "smooth",
  });
}

/** 把 ISO 时间转成「刚刚 / 3 小时前 / 2 天前」这类人话。 */
export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diffMin = Math.round((Date.now() - then) / 60000);

  if (diffMin < 1) return "刚刚";
  if (diffMin < 60) return `${diffMin} 分钟前`;
  if (diffMin < 60 * 24) return `${Math.round(diffMin / 60)} 小时前`;
  const days = Math.round(diffMin / 60 / 24);
  if (days < 30) return `${days} 天前`;
  return new Date(iso).toLocaleDateString("zh-CN");
}
