import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { readStored, writeStored } from "../lib/storage";
import type { Lang } from "./pick";
import { STRINGS, type Dict } from "./strings";

const STORAGE_KEY = "inbrief.lang";

interface LangValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: Dict;
}

// Without a provider (isolated component tests) the app behaves as English.
const LangContext = createContext<LangValue>({ lang: "en", setLang: () => {}, t: STRINGS.en });

export function initialLang(): Lang {
  const saved = readStored(STORAGE_KEY);
  if (saved === "en" || saved === "zh") return saved;
  return typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("zh") ? "zh" : "en";
}

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initialLang);

  useEffect(() => {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  }, [lang]);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    writeStored(STORAGE_KEY, next);
  }, []);

  const value = useMemo(() => ({ lang, setLang, t: STRINGS[lang] }), [lang, setLang]);
  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useLang(): LangValue {
  return useContext(LangContext);
}

// Date/time formatting follows the chosen language.
export function localeOf(lang: Lang): string {
  return lang === "zh" ? "zh-CN" : "en";
}
