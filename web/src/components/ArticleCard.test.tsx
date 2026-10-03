import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ArticleCard } from "./ArticleCard";
import type { ArchiveArticleOut } from "../api/types";

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

  it("links out safely for http(s) urls", () => {
    render(<ArticleCard article={article()} />);
    const link = screen.getByRole("link", { name: /English title/ });
    expect(link).toHaveAttribute("href", "https://example.com/post");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("does not render a link for non-http urls", () => {
    render(<ArticleCard article={article({ url: "javascript:alert(1)" })} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /English title/ })).toBeInTheDocument();
  });

  it("omits the published time when it is not a valid date", () => {
    render(<ArticleCard article={article({ published_at: "not-a-date" })} />);
    expect(screen.queryByText(/invalid date/i)).not.toBeInTheDocument();
  });
});
