import { useLang } from "../i18n/LangContext";
import type { Lang } from "../i18n/pick";

const LANGS: { code: Lang; label: string; htmlLang: string }[] = [
  { code: "en", label: "EN", htmlLang: "en" },
  { code: "zh", label: "中文", htmlLang: "zh-CN" },
];

export function SettingsBar() {
  const { lang, setLang, t } = useLang();
  return (
    <div className="settings-bar" role="group" aria-label={t.settings}>
      <div className="segmented" role="group" aria-label={t.language}>
        {LANGS.map((l) => (
          <button
            key={l.code}
            type="button"
            lang={l.htmlLang}
            className="segment"
            aria-pressed={lang === l.code}
            onClick={() => setLang(l.code)}
          >
            {l.label}
          </button>
        ))}
      </div>
    </div>
  );
}
