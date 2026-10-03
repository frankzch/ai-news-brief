import { useState } from "react";
import type { TagOut } from "../api/types";
import { useLang } from "../i18n/LangContext";

const COLLAPSED_COUNT = 15;

interface Props {
  tags: TagOut[];
  active: string | null;
  onChange: (tag: string | null) => void;
}

export function TagChips({ tags, active, onChange }: Props) {
  const [expanded, setExpanded] = useState(false);
  const { t } = useLang();
  // A shared URL may carry a tag outside the hot-tags list; keep it visible so it can be cleared.
  const all = active && !tags.some((t) => t.tag === active) ? [{ tag: active, count: 0 }, ...tags] : tags;
  if (all.length === 0) return null;
  const collapsible = all.length > COLLAPSED_COUNT;
  // The API returns tags by popularity; collapsed view keeps the top ones plus whatever is active.
  const shown =
    collapsible && !expanded ? all.filter((t, i) => i < COLLAPSED_COUNT || t.tag === active) : all;
  return (
    <div role="group" aria-label={t.tags} className="chips tag-filter">
      {shown.map((tag) => (
        <button
          key={tag.tag}
          type="button"
          className="chip chip-button"
          aria-pressed={active === tag.tag}
          onClick={() => onChange(active === tag.tag ? null : tag.tag)}
        >
          {tag.tag}
          {tag.count > 0 && <span className="chip-count"> {tag.count}</span>}
        </button>
      ))}
      {collapsible && (
        <button type="button" className="chip chip-toggle" onClick={() => setExpanded((e) => !e)}>
          {expanded ? t.showFewerTags : t.showAllTags(all.length)}
        </button>
      )}
    </div>
  );
}
