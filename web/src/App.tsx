import { useCallback, useEffect, useRef, useState } from "react";
import { clearApiCache, fetchCommandCenter } from "./api";
import { AppShell, AppView } from "./components/AppShell";
import { CommandCenter } from "./components/CommandCenter";
import { Circuits } from "./components/Circuits";
import { FleetWorkspace } from "./components/FleetWorkspace";
import { LiveryCollection } from "./components/LiveryCollection";
import { Network } from "./components/Network";
import { Ops } from "./components/Ops";
import { Pricing } from "./components/Pricing";
import { ShmMonitor } from "./components/ShmMonitor";
import { CommandCenterSnapshot } from "./types";

const ROUTES: AppView[] = ["command", "network", "circuits", "pricing", "fleet", "liveries", "shm", "ops"];

function readLocation(): { view: AppView; preset?: string } {
  const raw = window.location.hash.replace(/^#/, "");
  const [rawRoute, query = ""] = raw.split("?");
  // The aircraft editor used to be its own "hangar" tab; old links still land.
  const route = rawRoute === "hangar" ? "fleet" : rawRoute;
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

  // An aircraft opened from Network or Circuits keeps that tab mounted (hidden)
  // underneath, so coming back finds the same route or circuit, filters and
  // scroll position instead of a freshly reset tab.
  const [aircraftFrom, setAircraftFrom] = useState<"network" | "circuits" | null>(null);
  const originScrollRef = useRef(0);
  const viewingAircraft = view === "fleet" && Boolean(fleetPreset?.startsWith("ac:"));
  const openAircraftFrom = (origin: "network" | "circuits") => (aircraftId: number) => {
    originScrollRef.current = window.scrollY;
    setAircraftFrom(origin);
    // Switch now rather than on hashchange, or the effect below would see the
    // origin tab still active and drop aircraftFrom straight away.
    setView("fleet");
    setFleetPreset(`ac:${aircraftId}`);
    navigate("fleet", `ac:${aircraftId}`);
  };
  useEffect(() => {
    if (!aircraftFrom || viewingAircraft) return;
    if (view === aircraftFrom) window.scrollTo({ top: originScrollRef.current });
    setAircraftFrom(null);
  }, [view, viewingAircraft, aircraftFrom]);
  const keepAlive = (origin: "network" | "circuits") =>
    view === origin || (viewingAircraft && aircraftFrom === origin);
  const shownIf = (visible: boolean) => ({ display: visible ? "contents" : "none" });

  // A preset is a one-shot instruction ("open this livery", "show the idle
  // planes"), so once a workspace lets it go the URL drops it too -- otherwise
  // a reload would silently reapply a view the operator already dismissed.
  const clearPreset = () => {
    setFleetPreset(undefined);
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${view}`);
  };

  // Every cached snapshot is suspect once anything is written or the operator
  // asks for a refresh, so drop the cache before the views remount and refetch.
  const dataChanged = () => {
    clearApiCache();
    setRefreshToken((value) => value + 1);
    loadSnapshot(true);
  };

  return (
    <AppShell
      activeView={view}
      snapshot={snapshot}
      refreshing={refreshing}
      onNavigate={(next) => navigate(next)}
      onRefresh={dataChanged}
    >
      {keepAlive("network") && (
        <div style={shownIf(view === "network")}>
          <Network refreshToken={refreshToken} onOpenAircraft={openAircraftFrom("network")} />
        </div>
      )}
      {keepAlive("circuits") && (
        <div style={shownIf(view === "circuits")}>
          <Circuits refreshToken={refreshToken} onOpenAircraft={openAircraftFrom("circuits")} />
        </div>
      )}
      {view === "command" ? (
        <CommandCenter snapshot={snapshot} loading={loading} error={error} onNavigate={navigate} />
      ) : view === "network" || view === "circuits" ? null : view === "pricing" ? (
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
          onOpenAircraft={(aircraftId) => navigate("fleet", `ac:${aircraftId}`)}
          onPresetCleared={aircraftFrom && viewingAircraft ? () => navigate(aircraftFrom) : clearPreset}
          closeLabel={aircraftFrom === "network" ? "Back to route" : aircraftFrom === "circuits" ? "Back to circuit" : undefined}
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
