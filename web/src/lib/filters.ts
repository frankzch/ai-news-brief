export const CATEGORIES = ["news", "discussion", "video", "opensource"] as const;
export type Category = (typeof CATEGORIES)[number];

export interface Filters {
  category: Category | null;
  tag: string | null;
  q: string;
  page: number;
}

export const PAGE_SIZE = 30;

function isCategory(v: string | null): v is Category {
  return v !== null && (CATEGORIES as readonly string[]).includes(v);
}

export function parseFilters(params: URLSearchParams): Filters {
  const category = params.get("category");
  const tag = params.get("tag")?.trim() || null;
  const q = params.get("q")?.trim() ?? "";
  const rawPage = params.get("page") ?? "";
  const page = /^\d+$/.test(rawPage) ? Number(rawPage) : 1;
  return { category: isCategory(category) ? category : null, tag, q, page: page >= 1 ? page : 1 };
}

export function toSearchParams(f: Filters): URLSearchParams {
  const sp = new URLSearchParams();
  if (f.category) sp.set("category", f.category);
  if (f.tag) sp.set("tag", f.tag);
  if (f.q) sp.set("q", f.q);
  if (f.page > 1) sp.set("page", String(f.page));
  return sp;
}

// Any filter change goes back to page 1 unless a page is given explicitly.
export function withFilter(current: Filters, change: Partial<Filters>): Filters {
  return { ...current, page: 1, ...change };
}
