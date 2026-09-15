import { NetworkCircuit, PriceClass } from "./types";

/** The four fare classes in the game's own column order. */
export const PRICE_CLASSES: PriceClass[] = ["eco", "bus", "first", "cargo"];
export const CLASS_LABELS: Record<PriceClass, string> = { eco: "Economy", bus: "Business", first: "First", cargo: "Cargo" };

export function sumClasses(values: Partial<Record<PriceClass, number>>): number {
  return PRICE_CLASSES.reduce((sum, cls) => sum + (values[cls] ?? 0), 0);
}

/** Daily seats (or cargo tons) a route gets from its circuits: both directions,
 *  once per wave per day. */
export function dailyCapacity(circuits: NetworkCircuit[]): Record<PriceClass, number> {
  const total = { eco: 0, bus: 0, first: 0, cargo: 0 };
  for (const circuit of circuits) {
    total.eco += circuit.seats.eco * 2 * circuit.waves;
    total.bus += circuit.seats.bus * 2 * circuit.waves;
    total.first += circuit.seats.fir * 2 * circuit.waves;
    total.cargo += circuit.seats.cargo * 2 * circuit.waves;
  }
  return total;
}
