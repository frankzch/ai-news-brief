import { describe, expect, it } from "vitest";
import { render as rtlRender, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import { ArticleCard } from "./ArticleCard";
import type { ArchiveArticleOut } from "../api/types";

function render(ui: ReactElement) {
  return rtlRender(<MemoryRouter>{ui}</MemoryRouter>);
}

function article(over: Partial<ArchiveArticleOut> = {}): ArchiveArticleOut {
  return {
    article_id: "a1",
    rank: 1,
    title: "Raw title",
    title_en: "English title",
    title_zh: null,
    summary_en: "A short summary.",
    summary_zh: null,
    long_summary_en: null,
    long_summary_zh: null,
    importance_score: 8,
    relevance_score: 0,
    source: "Example Feed",
    category: "news",
    published_at: "2026-10-03T08:00:00Z",
    url: "https://example.com/post",
    labels: ["llm", "agents"],
    ...over,
  };
}

describe("ArticleCard", () => {
  it("shows rank, title, summary, source, category, tags and importance", () => {
    render(<ArticleCard article={article()} />);
    expect(screen.getByRole("heading", { name: /English title/ })).toBeInTheDocument();
    expect(screen.getByText("A short summary.")).toBeInTheDocument();
    expect(screen.getByText("Example Feed")).toBeInTheDocument();
    expect(screen.getByText("news")).toBeInTheDocument();
    expect(screen.getByText("llm")).toBeInTheDocument();
    expect(screen.getByText("agents")).toBeInTheDocument();
    expect(screen.getByText(/importance 8/i)).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("falls back to title and omits a missing summary", () => {
    render(<ArticleCard article={article({ title_en: null, summary_en: null })} />);
    expect(screen.getByRole("heading", { name: /Raw title/ })).toBeInTheDocument();
    expect(screen.queryByText("A short summary.")).not.toBeInTheDocument();
  });

  it("links the title to the internal detail page", () => {
    render(<ArticleCard article={article()} />);
    expect(screen.getByRole("link", { name: /English title/ })).toHaveAttribute("href", "/articles/a1");
  });

  it("encodes the article id in the detail link", () => {
    render(<ArticleCard article={article({ article_id: "a/b" })} />);
    expect(screen.getByRole("link", { name: /English title/ })).toHaveAttribute("href", "/articles/a%2Fb");
  });

  it("links out safely to the original for http(s) urls", () => {
    render(<ArticleCard article={article()} />);
    const link = screen.getByRole("link", { name: /original/i });
    expect(link).toHaveAttribute("href", "https://example.com/post");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("does not render an original link for non-http urls", () => {
    render(<ArticleCard article={article({ url: "javascript:alert(1)" })} />);
    expect(screen.queryByRole("link", { name: /original/i })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /English title/ })).toBeInTheDocument();
  });

  it("hides the rank outside a digest", () => {
    render(<ArticleCard article={article({ rank: 0 })} />);
    expect(screen.queryByLabelText(/rank/i)).not.toBeInTheDocument();
  });

  it("omits the published time when it is not a valid date", () => {
    render(<ArticleCard article={article({ published_at: "not-a-date" })} />);
    expect(screen.queryByText(/invalid date/i)).not.toBeInTheDocument();
  });
});
