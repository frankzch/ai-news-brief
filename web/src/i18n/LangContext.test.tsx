import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { LangProvider } from "./LangContext";
import { Layout } from "../components/Layout";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  document.documentElement.lang = "";
});

function renderLayout() {
  return render(
    <LangProvider>
      <MemoryRouter>
        <Layout />
      </MemoryRouter>
    </LangProvider>,
  );
}

describe("LangProvider", () => {
  it("defaults to English and sets <html lang>", () => {
    renderLayout();
    expect(screen.getByRole("link", { name: "Browse" })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("en");
  });

  it("switches UI text and <html lang> when toggled", async () => {
    renderLayout();
    await userEvent.click(screen.getByRole("button", { name: "中文" }));
    expect(screen.getByRole("link", { name: "浏览" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "中文" })).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement.lang).toBe("zh-CN");
  });

  it("persists the choice across remounts", async () => {
    const first = renderLayout();
    await userEvent.click(screen.getByRole("button", { name: "中文" }));
    first.unmount();
    renderLayout();
    expect(screen.getByRole("link", { name: "浏览" })).toBeInTheDocument();
  });

  it("ignores an invalid saved value", () => {
    localStorage.setItem("inbrief.lang", "fr");
    renderLayout();
    expect(screen.getByRole("link", { name: "Browse" })).toBeInTheDocument();
  });

  it("defaults to Chinese for a Chinese browser, but a saved choice wins", () => {
    vi.spyOn(navigator, "language", "get").mockReturnValue("zh-TW");
    const first = renderLayout();
    expect(screen.getByRole("link", { name: "浏览" })).toBeInTheDocument();
    first.unmount();
    localStorage.setItem("inbrief.lang", "en");
    renderLayout();
    expect(screen.getByRole("link", { name: "Browse" })).toBeInTheDocument();
  });
});
