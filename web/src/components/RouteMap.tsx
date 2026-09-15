import { useEffect, useRef } from "react";
import { Minus, Plus } from "lucide-react";
import { GeoContext, GeoPermissibleObjects, geoDistance, geoGraticule10, geoNaturalEarth1, geoOrthographic, geoPath } from "d3-geo";
import { select } from "d3-selection";
import { zoom, zoomIdentity } from "d3-zoom";
import { feature } from "topojson-client";
import landTopology from "world-atlas/land-110m.json";
import { NetworkLine, NetworkSnapshot } from "../types";
import { Button } from "@/components/ui/button";

export type MapMode = "map" | "globe";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const LAND = feature(landTopology as any, (landTopology as any).objects.land) as GeoPermissibleObjects;
const SPHERE: GeoPermissibleObjects = { type: "Sphere" };
const GRATICULE = geoGraticule10();
const SPIN_DEG_PER_FRAME = 0.05;
const IDLE_BEFORE_SPIN_MS = 2500;

/** Canvas world map / globe of the route network. Flat map pans and zooms
 *  (d3-zoom); the globe drags to rotate, wheels to zoom, and spins slowly on
 *  its own once the mouse has been idle for a moment. Nothing here is React
 *  state: every interaction redraws straight onto the canvas. */
export function RouteMap({ routes, hubs: ownedHubs, mode }: { routes: NetworkLine[]; hubs: NetworkSnapshot["hubs"]; mode: MapMode }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Survives the effect re-running (route list or mode changed) so a toggle
  // does not throw away where the user had panned/zoomed/rotated to.
  const view = useRef({ transform: zoomIdentity, globeZoom: 1, rotate: [-15, -25, 0] as [number, number, number] });
  const zoomBy = useRef<(factor: number) => void>(() => {});

  useEffect(() => {
    const canvas = canvasRef.current!;
    const box = canvas.parentElement!;
    const ctx = canvas.getContext("2d")!;
    const styles = getComputedStyle(box);
    const color = (name: string) => styles.getPropertyValue(name).trim();
    const cyan = color("--cyan"), amber = color("--amber");

    const mapped = routes.filter((line) => line.origin && line.destination);
    const multiline = (owned: boolean): GeoPermissibleObjects => ({ type: "MultiLineString", coordinates: mapped.filter((line) => line.is_owned === owned).map((line) => [[line.origin!.lon, line.origin!.lat], [line.destination!.lon, line.destination!.lat]]) });
    const owned = multiline(true), planned = multiline(false);
    // Owned hubs with no scraped routes still belong on the map, so the hub
    // markers come from the hub list as well as from the drawn route origins.
    const hubs = [...new Map<string, [number, number]>([
      ...mapped.map((line) => [line.hub_iata, [line.origin!.lon, line.origin!.lat]] as [string, [number, number]]),
      ...ownedHubs.filter((hub) => hub.location).map((hub) => [hub.hub_iata, [hub.location!.lon, hub.location!.lat]] as [string, [number, number]]),
    ])];

    const projection = mode === "map" ? geoNaturalEarth1() : geoOrthographic().rotate(view.current.rotate);
    let width = 0, height = 0, dpr = 1, baseScale = 1;
    let transform = view.current.transform;   // flat map pan/zoom
    let globeZoom = view.current.globeZoom;   // globe wheel zoom
    let dragging = false, lastTouch = performance.now(), frame = 0, spinFrame = 0, layers: Record<string, Path2D> | null = null;

    // geoPath streams into anything with moveTo/lineTo/arc/closePath, so a
    // Path2D works as its context; the flat map builds these once and lets the
    // canvas transform do the zooming, the globe rebuilds them per frame.
    const build = () => {
      const trace = (object: GeoPermissibleObjects) => { const path = new Path2D(); geoPath(projection, path as unknown as GeoContext)(object); return path; };
      return { sphere: trace(SPHERE), graticule: trace(GRATICULE), land: trace(LAND), owned: trace(owned), planned: trace(planned) };
    };

    const draw = () => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      let k = 1;
      if (mode === "map") { ctx.translate(transform.x, transform.y); ctx.scale(transform.k, transform.k); k = transform.k; layers ??= build(); }
      else { projection.scale(baseScale * globeZoom); layers = build(); }
      const px = (n: number) => n / k;
      // Skia rasterises a stroke that lands on exactly one device pixel with a
      // fast hairline path, and turns anything wider into a filled polygon per
      // subpath -- which at ~1000 route segments a frame is the whole cost of
      // the globe (measured 31ms -> 3ms of raster at dpr 2). Dashes are free
      // once the stroke is a hairline, so the planned routes keep theirs.
      const hair = 1 / (dpr * k);
      ctx.fillStyle = "#0b1219"; ctx.fill(layers.sphere);
      ctx.strokeStyle = "#1f2a35"; ctx.lineWidth = hair; ctx.stroke(layers.graticule);
      ctx.fillStyle = "#1d2930"; ctx.fill(layers.land);
      ctx.strokeStyle = "#35454e"; ctx.lineWidth = hair; ctx.stroke(layers.land);
      ctx.globalAlpha = .6; ctx.strokeStyle = amber; ctx.lineWidth = hair; ctx.setLineDash([px(4), px(3)]); ctx.stroke(layers.planned); ctx.setLineDash([]);
      ctx.globalAlpha = .45; ctx.strokeStyle = cyan; ctx.lineWidth = hair; ctx.stroke(layers.owned);
      ctx.globalAlpha = 1; ctx.strokeStyle = "#425262"; ctx.lineWidth = px(1.4); ctx.stroke(layers.sphere);
      ctx.font = `800 ${px(11)}px ${styles.fontFamily}`;
      const [lon, lat] = projection.rotate();
      for (const [iata, coords] of hubs) {
        if (mode === "globe" && geoDistance(coords, [-lon, -lat]) > Math.PI / 2 - .01) continue;
        const point = projection(coords);
        if (!point) continue;
        ctx.beginPath(); ctx.arc(point[0], point[1], px(3.5), 0, Math.PI * 2);
        ctx.fillStyle = amber; ctx.fill(); ctx.strokeStyle = "#0b0e13"; ctx.lineWidth = px(1.5); ctx.stroke();
        ctx.lineWidth = px(3); ctx.strokeText(iata, point[0] + px(6), point[1] - px(6));
        ctx.fillStyle = "#f3d29d"; ctx.fillText(iata, point[0] + px(6), point[1] - px(6));
      }
      view.current = { transform, globeZoom, rotate: mode === "globe" ? projection.rotate() : view.current.rotate };
    };

    // Pointer, wheel and resize events all outrun the display -- a 120 Hz
    // trackpad fires twice a frame -- so a redraw is requested, not performed.
    const schedule = () => { frame ||= requestAnimationFrame(() => { frame = 0; draw(); }); };

    const zoomBehavior = zoom<HTMLCanvasElement, unknown>().scaleExtent([1, 16]).on("zoom", (event) => { transform = event.transform; schedule(); });
    const fit = () => {
      dpr = window.devicePixelRatio || 1;
      width = box.clientWidth; height = box.clientHeight;
      canvas.width = width * dpr; canvas.height = height * dpr;
      projection.fitExtent([[10, 10], [width - 10, height - 10]], SPHERE);
      baseScale = projection.scale();
      layers = null;
      zoomBehavior.translateExtent([[0, 0], [width, height]]);
      schedule();
    };
    const observer = new ResizeObserver(fit);
    observer.observe(box);

    const touch = () => { lastTouch = performance.now(); };
    const onDown = (event: PointerEvent) => { dragging = true; canvas.setPointerCapture(event.pointerId); touch(); };
    const onUp = () => { dragging = false; touch(); };
    const onMove = (event: PointerEvent) => {
      if (!dragging) return;
      const [lon, lat] = projection.rotate();
      const speed = .3 / globeZoom;
      projection.rotate([lon + event.movementX * speed, Math.max(-90, Math.min(90, lat - event.movementY * speed))]);
      touch(); schedule();
    };
    const onWheel = (event: WheelEvent) => { event.preventDefault(); globeZoom = Math.max(.7, Math.min(8, globeZoom * Math.exp(-event.deltaY * .0015))); touch(); schedule(); };
    const spin = () => {
      if (!dragging && performance.now() - lastTouch > IDLE_BEFORE_SPIN_MS) { const [lon, lat] = projection.rotate(); projection.rotate([lon + SPIN_DEG_PER_FRAME, lat]); schedule(); }
      spinFrame = requestAnimationFrame(spin);
    };

    zoomBy.current = (factor) => {
      if (mode === "map") zoomBehavior.scaleBy(select(canvas), factor);
      else { globeZoom = Math.max(.7, Math.min(8, globeZoom * factor)); touch(); schedule(); }
    };

    if (mode === "map") select(canvas).call(zoomBehavior).call(zoomBehavior.transform, transform);
    else {
      canvas.addEventListener("pointerdown", onDown); canvas.addEventListener("pointermove", onMove);
      canvas.addEventListener("pointerup", onUp); canvas.addEventListener("pointercancel", onUp);
      canvas.addEventListener("wheel", onWheel, { passive: false });
      spinFrame = requestAnimationFrame(spin);
    }

    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame); cancelAnimationFrame(spinFrame);
      select(canvas).on(".zoom", null);
      canvas.removeEventListener("pointerdown", onDown); canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp); canvas.removeEventListener("pointercancel", onUp);
      canvas.removeEventListener("wheel", onWheel);
    };
  }, [routes, ownedHubs, mode]);

  return <div className={`route-map is-${mode}`}>
    <canvas ref={canvasRef} role="img" aria-label={`${mode === "map" ? "World map" : "Globe"} of ${routes.length} routes`} />
    <div className="map-zoom">
      <Button onClick={() => zoomBy.current(1.5)} aria-label="Zoom in" title="Zoom in"><Plus size={16} /></Button>
      <Button onClick={() => zoomBy.current(1 / 1.5)} aria-label="Zoom out" title="Zoom out"><Minus size={16} /></Button>
    </div>
  </div>;
}
