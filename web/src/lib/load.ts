export type Load<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

export function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : "Request failed";
}
