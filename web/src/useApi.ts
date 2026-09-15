import { DependencyList, useEffect, useState } from "react";

/** Run `load` on mount and whenever `deps` change, dropping answers that land
 *  after the component has moved on. The last good `data` stays up while a
 *  reload runs, so a refresh never blanks the page. A null `load` waits. */
export function useApi<T>(load: (() => Promise<T>) | null, deps: DependencyList) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(load !== null);

  useEffect(() => {
    if (!load) return;
    let live = true;
    setLoading(true);
    setError(null);
    load()
      .then((result) => { if (live) setData(result); })
      .catch((reason) => { if (live) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
    // The caller's deps decide when to reload; `load` is a fresh closure each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading };
}
