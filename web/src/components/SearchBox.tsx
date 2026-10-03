import { useEffect, useId, useRef, useState } from "react";

const DEBOUNCE_MS = 300;

interface Props {
  value: string;
  onCommit: (q: string) => void;
}

// Typing is local and commits to the URL once settled; URL changes (back/forward) flow back in.
export function SearchBox({ value, onCommit }: Props) {
  const id = useId();
  const [text, setText] = useState(value);
  const committed = useRef(value);
  // The debounce timer outlives renders; always call the latest handler so it never applies stale filters.
  const latestOnCommit = useRef(onCommit);
  latestOnCommit.current = onCommit;

  useEffect(() => {
    if (value !== committed.current) {
      committed.current = value;
      setText(value);
    }
  }, [value]);

  function commit(next: string) {
    const trimmed = next.trim();
    if (trimmed === committed.current) return;
    committed.current = trimmed;
    latestOnCommit.current(trimmed);
  }

  useEffect(() => {
    const timer = setTimeout(() => commit(text), DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  return (
    <form
      className="search-box"
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        commit(text);
      }}
    >
      <label htmlFor={id}>Search</label>
      <input id={id} type="search" value={text} maxLength={200} onChange={(e) => setText(e.target.value)} />
    </form>
  );
}
