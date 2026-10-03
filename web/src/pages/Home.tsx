import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, getDates, getDigest } from "../api/client";
import type { DatesOut, DigestOut } from "../api/types";
import { ArticleCard } from "../components/ArticleCard";
import { DatePicker } from "../components/DatePicker";

type Load<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

// Digest results are tagged with their date so a render never pairs one date's cards with another's picker value.
type DigestLoad = { date: string } & Load<DigestOut>;

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : "Request failed";
}

function RetryError({ children, onRetry }: { children: string; onRetry: () => void }) {
  return (
    <div role="alert" className="notice notice-error">
      <p>{children}</p>
      <button type="button" className="button" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

export function Home() {
  const [dates, setDates] = useState<Load<DatesOut>>({ status: "loading" });
  const [digest, setDigest] = useState<DigestLoad | null>(null);
  const [datesAttempt, setDatesAttempt] = useState(0);
  const [digestAttempt, setDigestAttempt] = useState(0);
  const [params, setParams] = useSearchParams();
  const requested = params.get("date");

  useEffect(() => {
    const controller = new AbortController();
    setDates({ status: "loading" });
    getDates(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setDates({ status: "ready", data });
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setDates({ status: "error", message: messageOf(err) });
      });
    return () => controller.abort();
  }, [datesAttempt]);

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
    setDigest({ date: selected, status: "loading" });
    getDigest(selected, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setDigest({ date: selected, status: "ready", data });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        // A date with no stored digest is just an empty day, not a failure.
        if (err instanceof ApiError && err.status === 404) {
          setDigest({ date: selected, status: "ready", data: { date: selected, articles: [] } });
        } else {
          setDigest({ date: selected, status: "error", message: messageOf(err) });
        }
      });
    return () => controller.abort();
  }, [selected, digestAttempt]);

  if (dates.status === "loading") {
    return <p role="status">Loading digests…</p>;
  }
  if (dates.status === "error") {
    return (
      <RetryError onRetry={() => setDatesAttempt((n) => n + 1)}>
        {`Could not load digests: ${dates.message}`}
      </RetryError>
    );
  }
  if (dates.data.dates.length === 0) {
    return <p className="notice">No digests yet. Run the pipeline to create one.</p>;
  }

  const current = digest && digest.date === selected ? digest : null;
  return (
    <section>
      <h1>Daily digest</h1>
      {selected && (
        <DatePicker dates={dates.data.dates} value={selected} onChange={(date) => setParams({ date })} />
      )}
      {(!current || current.status === "loading") && <p role="status">Loading digest…</p>}
      {current?.status === "error" && (
        <RetryError onRetry={() => setDigestAttempt((n) => n + 1)}>
          {`Could not load digest: ${current.message}`}
        </RetryError>
      )}
      {current?.status === "ready" &&
        (current.data.articles.length === 0 ? (
          <p className="notice">No articles for this date.</p>
        ) : (
          <div className="cards">
            {[...current.data.articles]
              .sort((a, b) => a.rank - b.rank)
              .map((a) => (
                <ArticleCard key={a.article_id} article={a} />
              ))}
          </div>
        ))}
    </section>
  );
}
