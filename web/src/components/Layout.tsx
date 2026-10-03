import { Link, NavLink, Outlet } from "react-router-dom";
import { useLang } from "../i18n/LangContext";
import { SettingsBar } from "./SettingsBar";

export function Layout() {
  const { t } = useLang();
  return (
    <div className="layout">
      <header className="site-header">
        <Link to="/" className="brand">
          {t.brand}
        </Link>
        <nav aria-label={t.navMain} className="site-nav">
          <NavLink to="/" end>
            {t.navDigest}
          </NavLink>
          <NavLink to="/articles">{t.navBrowse}</NavLink>
        </nav>
        <SettingsBar />
      </header>
      <main className="site-main">
        <Outlet />
      </main>
      <footer className="site-footer">{t.footer}</footer>
    </div>
  );
}
