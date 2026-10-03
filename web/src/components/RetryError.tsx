export function RetryError({ children, onRetry }: { children: string; onRetry: () => void }) {
  return (
    <div role="alert" className="notice notice-error">
      <p>{children}</p>
      <button type="button" className="button" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}
