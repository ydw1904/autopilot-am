import { useCallback, useEffect, useState } from "react";
import { fetchCommandCenter } from "./api";
import { AppShell, AppView } from "./components/AppShell";
import { CommandCenter } from "./components/CommandCenter";
import { FleetWorkspace } from "./components/FleetWorkspace";
import { Hangar } from "./components/Hangar";
import { LiveryCollection } from "./components/LiveryCollection";
import { Network } from "./components/Network";
import { Ops } from "./components/Ops";
import { Pricing } from "./components/Pricing";
import { ShmMonitor } from "./components/ShmMonitor";
import { CommandCenterSnapshot } from "./types";

const ROUTES: AppView[] = ["command", "network", "pricing", "fleet", "hangar", "liveries", "shm", "ops"];

function readLocation(): { view: AppView; preset?: string } {
  const raw = window.location.hash.replace(/^#/, "");
  const [route, query = ""] = raw.split("?");
  const params = new URLSearchParams(query);
  return {
    view: (ROUTES as string[]).includes(route) ? (route as AppView) : "command",
    preset: params.get("preset") || undefined,
  };
}

export function App() {
  const initial = readLocation();
  const [view, setView] = useState<AppView>(initial.view);
  const [fleetPreset, setFleetPreset] = useState(initial.preset);
  const [snapshot, setSnapshot] = useState<CommandCenterSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  const loadSnapshot = useCallback(async (quiet = false) => {
    quiet ? setRefreshing(true) : setLoading(true);
    setError(null);
    try {
      setSnapshot(await fetchCommandCenter());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Command Center request failed");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { loadSnapshot(); }, [loadSnapshot]);

  useEffect(() => {
    const handleHash = () => {
      const location = readLocation();
      setView(location.view);
      setFleetPreset(location.preset);
    };
    window.addEventListener("hashchange", handleHash);
    return () => window.removeEventListener("hashchange", handleHash);
  }, []);

  const navigate = (next: AppView, preset?: string) => {
    const suffix = preset ? `?preset=${encodeURIComponent(preset)}` : "";
    window.location.hash = `${next}${suffix}`;
  };

  // A preset is a one-shot instruction ("open this livery", "show the idle
  // planes"), so once a workspace lets it go the URL drops it too -- otherwise
  // a reload would silently reapply a view the operator already dismissed.
  const clearPreset = () => {
    setFleetPreset(undefined);
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${view}`);
  };

  const dataChanged = () => {
    setRefreshToken((value) => value + 1);
    loadSnapshot(true);
  };

  return (
    <AppShell
      activeView={view}
      snapshot={snapshot}
      refreshing={refreshing}
      onNavigate={(next) => navigate(next)}
      onRefresh={() => { setRefreshToken((value) => value + 1); loadSnapshot(true); }}
    >
      {view === "command" ? (
        <CommandCenter snapshot={snapshot} loading={loading} error={error} onNavigate={navigate} />
      ) : view === "network" ? (
        <Network refreshToken={refreshToken} onOpenFleet={(preset) => navigate("fleet", preset)} />
      ) : view === "pricing" ? (
        <Pricing snapshot={snapshot} refreshToken={refreshToken} />
      ) : view === "ops" ? (
        <Ops refreshToken={refreshToken} />
      ) : view === "fleet" ? (
        <FleetWorkspace
          snapshot={snapshot}
          initialPreset={fleetPreset}
          refreshToken={refreshToken}
          onDataChanged={dataChanged}
          onOpenLivery={(skinId) => navigate("liveries", `skin:${skinId}`)}
          onOpenAircraft={(aircraftId) => navigate("hangar", `ac:${aircraftId}`)}
        />
      ) : view === "hangar" ? (
        <Hangar
          snapshot={snapshot}
          initialPreset={fleetPreset}
          refreshToken={refreshToken}
          onDataChanged={dataChanged}
        />
      ) : view === "liveries" ? (
        <LiveryCollection
          initialPreset={fleetPreset}
          refreshToken={refreshToken}
          onViewInFleet={(liveryName) => navigate("fleet", `livery:${liveryName}`)}
          onPresetCleared={clearPreset}
        />
      ) : (
        <ShmMonitor refreshToken={refreshToken} />
      )}
    </AppShell>
  );
}

export default App;
