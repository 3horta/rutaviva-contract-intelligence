import { FileText, LayoutDashboard, Menu, MessageSquareText, Moon, Sun } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { DemoModeBadge } from "../auth/DemoSignIn";
import { TenantSwitcher } from "../auth/TenantSwitcher";
import { TokenExpired, TokenRequired } from "../auth/TokenScreens";
import { useAuth } from "../auth/useAuth";
import { Button } from "../components/ui/Button";
import { USE_MOCKS } from "../config";
import { UploadQueueProvider } from "../features/upload/UploadQueueProvider";
import { useTheme } from "../theme/useTheme";
import styles from "./AppShell.module.css";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/documents", label: "Documents", icon: FileText },
  { to: "/chat", label: "Chat", icon: MessageSquareText },
] as const;

export function AppShell() {
  const { activeSession, activeTokenInvalid } = useAuth();
  const { mode, toggle } = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);

  let content;
  if (!activeSession) {
    content = <TokenRequired />;
  } else if (activeTokenInvalid) {
    content = <TokenExpired session={activeSession} />;
  } else {
    // Keyed by tenant: switching remounts every page (fresh queries) and the
    // upload queue (aborting what the previous tenant had not sent).
    content = (
      <UploadQueueProvider key={activeSession.tenantKey}>
        <Outlet />
      </UploadQueueProvider>
    );
  }

  return (
    <div className={`${styles.shell} ${menuOpen ? styles.menuOpen : ""}`}>
      <a href="#main" className={styles.skip}>
        Skip to content
      </a>

      <aside
        id="sidebar"
        className={`${styles.sidebar} no-print`}
        onKeyDown={(event) => event.key === "Escape" && setMenuOpen(false)}
      >
        <NavLink to="/dashboard" className={styles.brand} aria-label="Rutaviva Contract Intelligence, home">
          <span className={styles.brandMark} aria-hidden="true">
            R
          </span>
          <span className={styles.brandText}>
            Rutaviva
            <small>Contract Intelligence</small>
          </span>
        </NavLink>
        <nav aria-label="Main" className={styles.nav}>
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) =>
                isActive ? `${styles.navLink} ${styles.navLinkActive}` : styles.navLink
              }
            >
              <Icon size={18} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        {USE_MOCKS && <p className={styles.mockNote}>Demo mode: data is simulated in the browser.</p>}
      </aside>

      {menuOpen && (
        <div className={styles.scrim} aria-hidden="true" onClick={() => setMenuOpen(false)} />
      )}

      <div className={styles.body}>
        <header className={`${styles.topbar} no-print`}>
          <Button
            variant="text"
            iconOnly
            className={styles.menuButton}
            aria-label="Menu"
            aria-expanded={menuOpen}
            aria-controls="sidebar"
            onClick={() => setMenuOpen((open) => !open)}
            icon={<Menu size={20} aria-hidden="true" />}
          />
          <div className={styles.topbarTitle}>Supplier contracts</div>
          <div className={styles.topbarActions}>
            <DemoModeBadge />
            <TenantSwitcher />
            <button
              type="button"
              className={styles.themeToggle}
              aria-label={mode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              title={mode === "dark" ? "Light mode" : "Dark mode"}
              onClick={toggle}
            >
              {mode === "dark" ? <Sun size={17} aria-hidden="true" /> : <Moon size={17} aria-hidden="true" />}
            </button>
          </div>
        </header>
        <main id="main" className={styles.content} tabIndex={-1}>
          {content}
        </main>
      </div>
    </div>
  );
}
