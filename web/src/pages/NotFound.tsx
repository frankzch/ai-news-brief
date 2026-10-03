import { Link } from "react-router-dom";
import { useLang } from "../i18n/LangContext";

export function NotFound() {
  const { t } = useLang();
  return (
    <section>
      <h1>{t.pageNotFound}</h1>
      <p>
        <Link to="/">{t.backToDigest}</Link>
      </p>
    </section>
  );
}
