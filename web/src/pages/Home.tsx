import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getDates, getDigest } from "../api/client";
import type { DatesOut, DigestOut } from "../api/types";
import { ArticleCard } from "../components/ArticleCard";
import { DatePicker } from "../components/DatePicker";

type Load<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : "Request failed";
}

export function Home() {
  const [dates, setDates] = useState<Load<DatesOut>>({ status: "loading" });
  const [digest, setDigest] = useState<Load<DigestOut>>({ status: "loading" });
  const [params, setParams] = useSearchParams();
  const requested = params.get("date");

  useEffect(() => {
    const controller = new AbortController();
    getDates(controller.signal)
      .then((data) => setDates({ status: "ready", data }))
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setDates({ status: "error", message: messageOf(err) });
      });
    return () => controller.abort();
  }, []);

  // Only dates the API reported are selectable; anything else falls back to the latest.
  const available = dates.status === "ready" ? dates.data : null;
  const selected =
    available && requested && available.dates.includes(requested) ? requested : (available?.latest ?? null);

  useEffect(() => {
    if (selected && selected !== requested) setParams({ date: selected }, { replace: true });
  }, [selected, requested, setParams]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setDigest({ status: "loading" });
    getDigest(selected, controller.signal)
      .then((data) => setDigest({ status: "ready", data }))
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setDigest({ status: "error", message: messageOf(err) });
      });
    return () => controller.abort();
  }, [selected]);

  if (dates.status === "loading") {
    return <p role="status">Loading digests…</p>;
  }
  if (dates.status === "error") {
    return <p role="alert" className="notice notice-error">Could not load digests: {dates.message}</p>;
  }
  if (dates.data.dates.length === 0) {
    return <p className="notice">No digests yet. Run the pipeline to create one.</p>;
  }
  return (
    <section>
      <h1>Daily digest</h1>
      {selected && (
        <DatePicker dates={dates.data.dates} value={selected} onChange={(date) => setParams({ date })} />
      )}
      {digest.status === "loading" && <p role="status">Loading digest…</p>}
      {digest.status === "error" && (
        <p role="alert" className="notice notice-error">Could not load digest: {digest.message}</p>
      )}
      {digest.status === "ready" && (
        <div className="cards">
          {[...digest.data.articles]
            .sort((a, b) => a.rank - b.rank)
            .map((a) => (
              <ArticleCard key={a.article_id} article={a} />
            ))}
        </div>
      )}
    </section>
  );
}
