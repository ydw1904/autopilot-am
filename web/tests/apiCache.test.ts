import { expect, test } from "bun:test";
import { clearApiCache, fetchNetwork, fetchPricing } from "../src/api";

/** Swap in a counting fetch; returns the call counter. */
function stubFetch(ok: () => boolean): { calls: number } {
  const counter = { calls: 0 };
  globalThis.fetch = (async () => {
    counter.calls += 1;
    return { ok: ok(), json: async () => ({ routes: [], hubs: [] }) } as Response;
  }) as typeof fetch;
  return counter;
}

test("a repeat snapshot read is served from the cache until it is cleared", async () => {
  const counter = stubFetch(() => true);
  clearApiCache();

  await fetchNetwork();
  await fetchNetwork();
  expect(counter.calls).toBe(1);

  clearApiCache();
  await fetchNetwork();
  expect(counter.calls).toBe(2);
});

test("a failed snapshot read is not cached", async () => {
  let healthy = false;
  const counter = stubFetch(() => healthy);
  clearApiCache();

  expect(fetchNetwork()).rejects.toThrow();
  await Bun.sleep(0);

  healthy = true;
  await fetchNetwork();
  expect(counter.calls).toBe(2);
});

test("live pricing is cached per hub, so refilling the network table is free", async () => {
  const counter = stubFetch(() => true);
  clearApiCache();

  await Promise.all([fetchPricing("CGK"), fetchPricing("CGK"), fetchPricing("MPM")]);
  expect(counter.calls).toBe(2);

  await fetchPricing("CGK");
  expect(counter.calls).toBe(2);
});
