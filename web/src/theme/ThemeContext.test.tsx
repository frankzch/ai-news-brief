import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { LangProvider } from "../i18n/LangContext";
import { Layout } from "../components/Layout";
import { ThemeProvider } from "./ThemeContext";

afterEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

function renderLayout() {
  return render(
    <ThemeProvider>
      <LangProvider>
        <MemoryRouter>
          <Layout />
        </MemoryRouter>
      </LangProvider>
    </ThemeProvider>,
  );
}

const root = () => document.documentElement;

describe("ThemeProvider", () => {
  it("defaults to system with no data-theme attribute", () => {
    renderLayout();
    expect(root()).not.toHaveAttribute("data-theme");
    expect(screen.getByRole("button", { name: "System" })).toHaveAttribute("aria-pressed", "true");
  });

  it("applies and persists a manual override", async () => {
    const first = renderLayout();
    await userEvent.click(screen.getByRole("button", { name: "Dark" }));
    expect(root()).toHaveAttribute("data-theme", "dark");
    first.unmount();
    renderLayout();
    expect(root()).toHaveAttribute("data-theme", "dark");
    expect(screen.getByRole("button", { name: "Dark" })).toHaveAttribute("aria-pressed", "true");
  });

  it("returns to system by removing the attribute", async () => {
    renderLayout();
    await userEvent.click(screen.getByRole("button", { name: "Light" }));
    expect(root()).toHaveAttribute("data-theme", "light");
    await userEvent.click(screen.getByRole("button", { name: "System" }));
    expect(root()).not.toHaveAttribute("data-theme");
  });

  it("ignores an invalid saved value and labels controls in Chinese", async () => {
    localStorage.setItem("inbrief.theme", "sepia");
    localStorage.setItem("inbrief.lang", "zh");
    renderLayout();
    expect(root()).not.toHaveAttribute("data-theme");
    expect(screen.getByRole("button", { name: "深色" })).toBeInTheDocument();
  });
});
