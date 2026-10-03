import { CATEGORIES, type Category } from "../lib/filters";

interface Props {
  value: Category | null;
  onChange: (category: Category | null) => void;
}

export function CategoryTabs({ value, onChange }: Props) {
  return (
    <div role="group" aria-label="Category" className="tabs">
      <button type="button" className="tab" aria-pressed={value === null} onClick={() => onChange(null)}>
        All
      </button>
      {CATEGORIES.map((c) => (
        <button
          key={c}
          type="button"
          className="tab"
          aria-pressed={value === c}
          onClick={() => onChange(value === c ? null : c)}
        >
          {c}
        </button>
      ))}
    </div>
  );
}
