import { useLang } from "../i18n/LangContext";
import { CATEGORIES, type Category } from "../lib/filters";

interface Props {
  value: Category | null;
  onChange: (category: Category | null) => void;
}

export function CategoryTabs({ value, onChange }: Props) {
  const { t } = useLang();
  return (
    <div role="group" aria-label={t.category} className="tabs">
      <button type="button" className="tab" aria-pressed={value === null} onClick={() => onChange(null)}>
        {t.categoryAll}
      </button>
      {CATEGORIES.map((c) => (
        <button
          key={c}
          type="button"
          className="tab"
          aria-pressed={value === c}
          onClick={() => onChange(value === c ? null : c)}
        >
          {t.categories[c] ?? c}
        </button>
      ))}
    </div>
  );
}
