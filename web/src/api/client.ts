import type { DatesOut, DigestOut } from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// Same-origin only: in dev Vite proxies /api to the loopback API.
export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  if (!path.startsWith("/api/")) {
    throw new Error(`API paths must start with /api/: ${path}`);
  }
  const res = await fetch(path, { method: "GET", headers: { Accept: "application/json" }, signal });
  if (!res.ok) {
    let message = res.statusText || `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // non-JSON error body; keep the status text
    }
    throw new ApiError(res.status, message);
  }
  return (await res.json()) as T;
}

export function getDates(signal?: AbortSignal): Promise<DatesOut> {
  return getJson<DatesOut>("/api/dates", signal);
}

export function getDigest(date: string, signal?: AbortSignal): Promise<DigestOut> {
  return getJson<DigestOut>(`/api/digest/${encodeURIComponent(date)}`, signal);
}
