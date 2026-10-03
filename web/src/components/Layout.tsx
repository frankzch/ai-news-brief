import { Link, Outlet } from "react-router-dom";

export function Layout() {
  return (
    <div className="layout">
      <header className="site-header">
        <Link to="/" className="brand">
          AI News Brief
        </Link>
      </header>
      <main className="site-main">
        <Outlet />
      </main>
      <footer className="site-footer">Local read-only view of your daily digest</footer>
    </div>
  );
}
