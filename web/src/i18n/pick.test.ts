import { describe, expect, it } from "vitest";
import { pick } from "./pick";

describe("pick", () => {
  it("prefers the chosen language", () => {
    expect(pick("zh", { en: "Hello", zh: "你好" })).toBe("你好");
    expect(pick("en", { en: "Hello", zh: "你好" })).toBe("Hello");
  });

  it("falls back to the other language when missing", () => {
    expect(pick("zh", { en: "Hello", zh: null })).toBe("Hello");
    expect(pick("en", { en: null, zh: "你好" })).toBe("你好");
  });

  it("treats empty and whitespace-only strings as missing", () => {
    expect(pick("zh", { en: "Hello", zh: "   " })).toBe("Hello");
    expect(pick("en", { en: "", zh: "你好" })).toBe("你好");
  });

  it("uses the generic fallback last, then null", () => {
    expect(pick("zh", { en: null, zh: null }, "Raw")).toBe("Raw");
    expect(pick("en", { en: null, zh: null })).toBeNull();
  });
});
