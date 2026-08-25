import React, { useEffect, useState } from "react";
import {
  Activity,
  ArrowUp,
  Gauge,
  Palette,
  Plane,
  Plug,
  Radar,
  RefreshCcw,
  Route,
  Settings2,
  Sparkles,
} from "lucide-react";
import { CommandCenterSnapshot } from "../types";
import { launchBrowser } from "../api";

export type AppView = "command" | "fleet" | "liveries" | "shm";

interface AppShellProps {
  activeView: AppView;
  snapshot: CommandCenterSnapshot | null;
  refreshing: boolean;
  onNavigate: (view: AppView) => void;
  onRefresh: () => void;
  children: React.ReactNode;
}

const navigation = [
  { id: "command" as const, label: "Command", icon: Gauge },
  { id: "fleet" as const, label: "Fleet", icon: Plane },
  { id: "liveries" as const, label: "Liveries", icon: Palette },
  { id: "shm" as const, label: "SHM", icon: Radar },
];

const pageCopy: Record<AppView, { title: string; subtitle: string }> = {
  command: { title: "Command Center", subtitle: "Portfolio readiness and execution queue" },
  fleet: { title: "Fleet Operations", subtitle: "Inspect and organize every aircraft from one workspace" },
  liveries: { title: "Livery Collection", subtitle: "Track every special paint scheme across the fleet" },
  shm: { title: "SHM Watcher", subtitle: "Track market coverage, sightings, and purchase decisions" },
};

export function AppShell({
  activeView,
  snapshot,
  refreshing,
  onNavigate,
  onRefresh,
  children,
}: AppShellProps) {
  const connected = snapshot?.status.browser_connected ?? false;
  const mobileConfigured = snapshot?.status.mobile_configured ?? false;
  const { title: pageTitle, subtitle: pageSubtitle } = pageCopy[activeView];
  const [showBackToTop, setShowBackToTop] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);

  const handleLaunch = async () => {
    if (launching) return;
    setLaunching(true);
    setLaunchError(null);
    try {
      const result = await launchBrowser();
      // Connected but not signed in (wrong Chrome / logged-out profile): keep
      // the warning visible so "Chrome connected" isn't mistaken for "ready".
      setLaunchError(result.browser_connected && !result.logged_in ? result.message : null);
    } catch (reason) {
      setLaunchError(reason instanceof Error ? reason.message : "Launch failed");
    } finally {
      setLaunching(false);
      onRefresh();
    }
  };

  useEffect(() => {
    const handleScroll = () => setShowBackToTop(window.scrollY > 400);
    window.addEventListener("scroll", handleScroll, { passive: true });
    handleScroll();
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <div className="app-frame">
      <aside className="nav-rail" aria-label="Primary navigation">
        <button className="brand-button" onClick={() => onNavigate("command")} aria-label="Autopilot home">
          <span className="brand-glyph"><Sparkles size={18} strokeWidth={2.4} /></span>
          <span className="brand-word">AM</span>
        </button>

        <nav className="rail-nav">
          {navigation.map((item) => {
            const Icon = item.icon;
            const active = activeView === item.id;
            return (
              <button
                key={item.id}
                className={`rail-link${active ? " is-active" : ""}`}
                onClick={() => onNavigate(item.id)}
                aria-current={active ? "page" : undefined}
              >
                <Icon size={20} strokeWidth={active ? 2.3 : 1.8} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="rail-future" aria-label="Upcoming workspaces">
          <Route size={17} />
          <Activity size={17} />
          <Settings2 size={17} />
        </div>
      </aside>

      <div className="app-surface">
        <header className="app-header">
          <div className="page-heading">
            <p className="page-kicker">Autopilot AM</p>
            <div className="page-title-row">
              <h1>{pageTitle}</h1>
              <span className="page-subtitle">{pageSubtitle}</span>
            </div>
          </div>

          <div className="header-actions">
            {launchError && (
              <div className="launch-warning" role="status" title={launchError}>
                <Plug size={13} />
                <span>{launchError}</span>
              </div>
            )}
            {connected ? (
              <div className="connection-pill is-online" title="Chrome CDP is connected for web-only game operations">
                <span className="connection-dot" />
                <span>Chrome connected</span>
              </div>
            ) : (
              <button
                className="connection-pill is-offline is-action"
                onClick={handleLaunch}
                disabled={launching}
                title={launchError || (mobileConfigured
                  ? "Limited mode: cached views and mobile fleet tools work. Link Chrome for web-only actions."
                  : "Limited mode: cached views work. Link Chrome for live web actions.")}
              >
                {launching ? <Plug size={13} className="is-spinning" /> : <span className="connection-dot" />}
                <span>{launching ? "Linking…" : "Limited mode"}</span>
              </button>
            )}
            <button className="icon-action" onClick={onRefresh} disabled={refreshing} title="Reload cached data">
              <RefreshCcw size={17} className={refreshing ? "is-spinning" : ""} />
              <span>Reload</span>
            </button>
          </div>
        </header>

        <main className="app-content">{children}</main>

        {showBackToTop && (
          <button
            className="back-to-top"
            onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
            aria-label="Back to top"
            title="Back to top"
          >
            <ArrowUp size={19} strokeWidth={2.4} />
          </button>
        )}
      </div>
    </div>
  );
}
