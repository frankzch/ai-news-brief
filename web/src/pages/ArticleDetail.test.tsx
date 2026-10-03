import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ArticleDetail } from "./ArticleDetail";
import type { ArticleOut } from "../api/types";
import { LangProvider } from "../i18n/LangContext";

const ART: ArticleOut = {
  id: "a1",
  title: "Raw title",
  title_en: "English title",
  title_zh: null,
  summary_en: "Short summary.",
  summary_zh: null,
  long_summary_en: "A much longer summary.",
  long_summary_zh: null,
  importance_score: 7,
  tags: ["llm", "agents"],
  category: "news",
  url: "https://example.com/post",
  source_name: "Example Feed",
  published_at: "2026-10-03T08:00:00Z",
};

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function stub(handler: () => Promise<Response>) {
  const fn = vi.fn((_path: string) => handler());
  vi.stubGlobal("fetch", fn);
  return fn;
}
const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));

function renderAt(path = "/articles/a1", state?: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: path, state }]}>
      <Routes>
        <Route path="/articles/:id" element={<ArticleDetail />} />
        <Route path="/articles" element={<p>list page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ArticleDetail", () => {
  it("shows loading, then the full article", async () => {
    const fetchFn = stub(() => json(ART));
    renderAt();
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
    expect(await screen.findByRole("heading", { level: 1, name: "English title" })).toBeInTheDocument();
    expect(fetchFn.mock.calls[0][0]).toBe("/api/articles/a1");
    expect(screen.getByText("A much longer summary.")).toBeInTheDocument();
    expect(screen.queryByText("Short summary.")).not.toBeInTheDocument();
    expect(screen.getByText("Example Feed")).toBeInTheDocument();
    expect(screen.getByText("news")).toBeInTheDocument();
    expect(screen.getByText("llm")).toBeInTheDocument();
    expect(screen.getByText("agents")).toBeInTheDocument();
    const time = document.querySelector("time");
    expect(time).toHaveAttribute("datetime", "2026-10-03T08:00:00.000Z");
    const link = screen.getByRole("link", { name: /open original/i });
    expect(link).toHaveAttribute("href", "https://example.com/post");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("falls back to the short summary", async () => {
    stub(() => json({ ...ART, long_summary_en: null }));
    renderAt();
    expect(await screen.findByText("Short summary.")).toBeInTheDocument();
  });

  it("does not link an unsafe original url", async () => {
    stub(() => json({ ...ART, url: "javascript:alert(1)" }));
    renderAt();
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("link", { name: /open original/i })).not.toBeInTheDocument();
  });

  it("hides an unparseable published time", async () => {
    stub(() => json({ ...ART, published_at: "garbage" }));
    renderAt();
    await screen.findByRole("heading", { level: 1 });
    expect(document.querySelector("time")).toBeNull();
  });

  it("shows not-found for a 404", async () => {
    stub(() => json({ detail: "Article not found" }, 404));
    renderAt();
    expect(await screen.findByText(/article not found/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows an error with Retry that refetches", async () => {
    let fail = true;
    const fetchFn = stub(() => (fail ? json({ detail: "boom" }, 500) : json(ART)));
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByRole("heading", { level: 1, name: "English title" })).toBeInTheDocument();
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it("back link returns to the list", async () => {
    stub(() => json(ART));
    renderAt();
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByRole("link", { name: /back/i })).toHaveAttribute("href", "/articles");
  });

  it("back link restores the originating list query", async () => {
    stub(() => json(ART));
    renderAt("/articles/a1", { from: "/articles?category=news&page=2" });
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByRole("link", { name: /back/i })).toHaveAttribute("href", "/articles?category=news&page=2");
  });

  it("ignores a back target that is not an in-app path", async () => {
    stub(() => json(ART));
    renderAt("/articles/a1", { from: "//evil.example/x" });
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByRole("link", { name: /back/i })).toHaveAttribute("href", "/articles");
  });
});

describe("ArticleDetail markdown", () => {
  it("renders markdown in the summary instead of raw markers", async () => {
    stub(() => json({ ...ART, long_summary_en: "## Why it matters\n\nThis is **big**." }));
    renderAt();
    expect(await screen.findByRole("heading", { level: 2, name: "Why it matters" })).toBeInTheDocument();
    expect(document.querySelector("strong")).toHaveTextContent("big");
    expect(document.body.textContent).not.toContain("**");
  });
});

describe("ArticleDetail language", () => {
  function renderZh() {
    localStorage.setItem("inbrief.lang", "zh");
    return render(
      <LangProvider>
        <MemoryRouter initialEntries={["/articles/a1"]}>
          <Routes>
            <Route path="/articles/:id" element={<ArticleDetail />} />
          </Routes>
        </MemoryRouter>
      </LangProvider>,
    );
  }

  it("shows the Chinese title, long summary and chrome", async () => {
    stub(() => json({ ...ART, title_zh: "中文标题", long_summary_zh: "较长的中文摘要。" }));
    renderZh();
    expect(await screen.findByRole("heading", { level: 1, name: "中文标题" })).toBeInTheDocument();
    expect(screen.getByText("较长的中文摘要。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "打开原文" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "← 返回文章列表" })).toBeInTheDocument();
  });

  it("falls back to the English long summary when Chinese is missing", async () => {
    stub(() => json(ART));
    renderZh();
    expect(await screen.findByRole("heading", { level: 1, name: "English title" })).toBeInTheDocument();
    expect(screen.getByText("A much longer summary.")).toBeInTheDocument();
  });

  it("uses the Chinese short summary when there is no long summary", async () => {
    stub(() => json({ ...ART, long_summary_en: null, summary_zh: "中文短摘要。" }));
    renderZh();
    expect(await screen.findByText("中文短摘要。")).toBeInTheDocument();
  });
});
