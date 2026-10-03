import { useLang } from "../i18n/LangContext";

interface Props {
  page: number;
  pages: number;
  onChange: (page: number) => void;
}

export function Pagination({ page, pages, onChange }: Props) {
  const { t } = useLang();
  if (pages <= 1) return null;
  return (
    <nav aria-label={t.pagination} className="pagination">
      <button type="button" className="button" disabled={page <= 1} onClick={() => onChange(page - 1)}>
        {t.previous}
      </button>
      <span>
        {t.pageOf(page, pages)}
      </span>
      <button type="button" className="button" disabled={page >= pages} onClick={() => onChange(page + 1)}>
        {t.next}
      </button>
    </nav>
  );
}
