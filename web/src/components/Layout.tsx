import { Link, NavLink, Outlet } from "react-router-dom";

export function Layout() {
  return (
    <div className="layout">
      <header className="site-header">
        <Link to="/" className="brand">
          AI News Brief
        </Link>
        <nav aria-label="Main" className="site-nav">
          <NavLink to="/" end>
            Digest
          </NavLink>
          <NavLink to="/articles">Browse</NavLink>
        </nav>
      </header>
      <main className="site-main">
        <Outlet />
      </main>
      <footer className="site-footer">Local read-only view of your daily digest</footer>
    </div>
  );
}
