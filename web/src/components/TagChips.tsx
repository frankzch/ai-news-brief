import type { TagOut } from "../api/types";

interface Props {
  tags: TagOut[];
  active: string | null;
  onChange: (tag: string | null) => void;
}

export function TagChips({ tags, active, onChange }: Props) {
  // A shared URL may carry a tag outside the hot-tags list; keep it visible so it can be cleared.
  const shown = active && !tags.some((t) => t.tag === active) ? [{ tag: active, count: 0 }, ...tags] : tags;
  if (shown.length === 0) return null;
  return (
    <div role="group" aria-label="Tags" className="chips tag-filter">
      {shown.map((t) => (
        <button
          key={t.tag}
          type="button"
          className="chip chip-button"
          aria-pressed={active === t.tag}
          onClick={() => onChange(active === t.tag ? null : t.tag)}
        >
          {t.tag}
          {t.count > 0 && <span className="chip-count"> {t.count}</span>}
        </button>
      ))}
    </div>
  );
}
