import { useEffect, useState } from "react";
import { getDates } from "../api/client";
import type { DatesOut } from "../api/types";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: DatesOut };

export function Home() {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    getDates(controller.signal)
      .then((data) => setState({ status: "ready", data }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setState({ status: "error", message: err instanceof Error ? err.message : "Request failed" });
      });
    return () => controller.abort();
  }, []);

  if (state.status === "loading") {
    return <p role="status">Loading digests…</p>;
  }
  if (state.status === "error") {
    return <p role="alert" className="notice notice-error">Could not load digests: {state.message}</p>;
  }
  const { dates, latest } = state.data;
  if (dates.length === 0) {
    return <p className="notice">No digests yet. Run the pipeline to create one.</p>;
  }
  return (
    <section>
      <h1>Daily digest</h1>
      <p>
        Latest digest: <time dateTime={latest ?? undefined}>{latest}</time>
      </p>
      <h2>Available dates</h2>
      <ul className="date-list">
        {dates.map((d) => (
          <li key={d}>
            <time dateTime={d}>{d}</time>
          </li>
        ))}
      </ul>
    </section>
  );
}
