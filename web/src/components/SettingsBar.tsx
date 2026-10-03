import { useLang } from "../i18n/LangContext";
import type { Lang } from "../i18n/pick";
import { useTheme, type ThemeChoice } from "../theme/ThemeContext";

const LANGS: { code: Lang; label: string; htmlLang: string }[] = [
  { code: "en", label: "EN", htmlLang: "en" },
  { code: "zh", label: "中文", htmlLang: "zh-CN" },
];

export function SettingsBar() {
  const { lang, setLang, t } = useLang();
  const { theme, setTheme } = useTheme();
  const themes: { code: ThemeChoice; label: string }[] = [
    { code: "system", label: t.themeSystem },
    { code: "light", label: t.themeLight },
    { code: "dark", label: t.themeDark },
  ];
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
      <div className="segmented" role="group" aria-label={t.theme}>
        {themes.map((o) => (
          <button
            key={o.code}
            type="button"
            className="segment"
            aria-pressed={theme === o.code}
            onClick={() => setTheme(o.code)}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}
