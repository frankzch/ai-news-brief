import { afterEach, describe, expect, it, vi } from "vitest";
import { readStored, writeStored } from "./storage";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("storage", () => {
  it("round-trips a value", () => {
    writeStored("k", "v");
    expect(readStored("k")).toBe("v");
  });

  it("returns null for a missing key", () => {
    expect(readStored("missing")).toBeNull();
  });

  it("never throws when localStorage is blocked", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
    });
    expect(readStored("k")).toBeNull();
    expect(() => writeStored("k", "v")).not.toThrow();
  });
});
