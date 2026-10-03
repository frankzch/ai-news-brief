import "@testing-library/jest-dom/vitest";

// Recent Node versions expose a non-functional global localStorage that shadows jsdom's; use a plain in-memory one.
if (typeof localStorage === "undefined" || typeof localStorage.clear !== "function") {
  const data = new Map<string, string>();
  const memory: Storage = {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (k) => data.get(k) ?? null,
    key: (i) => [...data.keys()][i] ?? null,
    removeItem: (k) => void data.delete(k),
    setItem: (k, v) => void data.set(k, String(v)),
  };
  Object.defineProperty(globalThis, "localStorage", { value: memory, configurable: true });
  Object.defineProperty(window, "localStorage", { value: memory, configurable: true });
}
