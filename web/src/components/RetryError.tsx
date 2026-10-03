import { useLang } from "../i18n/LangContext";

export function RetryError({ children, onRetry }: { children: string; onRetry: () => void }) {
  const { t } = useLang();
  return (
    <div role="alert" className="notice notice-error">
      <p>{children}</p>
      <button type="button" className="button" onClick={onRetry}>
        {t.retry}
      </button>
    </div>
  );
}
