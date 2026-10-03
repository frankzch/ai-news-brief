export type Lang = "en" | "zh";

function present(s: string | null | undefined): s is string {
  return typeof s === "string" && s.trim() !== "";
}

// Chosen language first, then the other one, then the generic field; null if nothing usable.
export function pick(
  lang: Lang,
  values: { en: string | null | undefined; zh: string | null | undefined },
  fallback?: string | null,
): string | null {
  const order = lang === "zh" ? [values.zh, values.en] : [values.en, values.zh];
  for (const v of order) if (present(v)) return v;
  return present(fallback) ? fallback : null;
}
