import { describe, expect, it } from "vitest";
import { parseFilters, toSearchParams, withFilter } from "./filters";

describe("parseFilters", () => {
  it("reads category, tag, q and page", () => {
    const f = parseFilters(new URLSearchParams("category=news&tag=llm&q=gpt&page=3"));
    expect(f).toEqual({ category: "news", tag: "llm", q: "gpt", page: 3 });
  });

  it("defaults when the query is empty", () => {
    expect(parseFilters(new URLSearchParams(""))).toEqual({ category: null, tag: null, q: "", page: 1 });
  });

  it("drops an unknown category", () => {
    expect(parseFilters(new URLSearchParams("category=bogus")).category).toBeNull();
  });

  it("clamps bad pages to 1", () => {
    for (const p of ["0", "-2", "abc", "1.5", ""]) {
      expect(parseFilters(new URLSearchParams(`page=${p}`)).page).toBe(1);
    }
  });

  it("treats blank tag/q as unset", () => {
    const f = parseFilters(new URLSearchParams("tag=%20&q=%20"));
    expect(f.tag).toBeNull();
    expect(f.q).toBe("");
  });
});

describe("toSearchParams", () => {
  it("omits defaults and encodes values", () => {
    const sp = toSearchParams({ category: "video", tag: "a b", q: "x&y", page: 2 });
    expect(sp.toString()).toBe("category=video&tag=a+b&q=x%26y&page=2");
    expect(toSearchParams({ category: null, tag: null, q: "", page: 1 }).toString()).toBe("");
  });

  it("round-trips through parseFilters", () => {
    const f = { category: "opensource" as const, tag: "中文", q: "héllo", page: 4 };
    expect(parseFilters(toSearchParams(f))).toEqual(f);
  });
});

describe("withFilter", () => {
  const base = { category: "news" as const, tag: "llm", q: "", page: 5 };

  it("changes one field and resets the page", () => {
    expect(withFilter(base, { tag: "agents" })).toEqual({ category: "news", tag: "agents", q: "", page: 1 });
  });

  it("can clear a field", () => {
    expect(withFilter(base, { category: null }).category).toBeNull();
  });

  it("keeps an explicit page", () => {
    expect(withFilter(base, { page: 6 }).page).toBe(6);
  });
});
