import { useEffect, useState } from "react";

// Tiny hook for the common "fetch-on-mount" pattern.
// `loader` should be a function that takes { signal } and returns a Promise.
// Pass `deps` to refetch when inputs change.
//
// We intentionally don't pull in TanStack Query yet — when we add caching,
// retries, or revalidation, swap this hook for `useQuery` and every call site
// keeps the same return shape: { data, loading, error, setData }.
//
// `setData` accepts either a new value or an updater function (same contract
// as React Query's setQueryData), letting callers reflect mutation responses
// in the local cache without a network round-trip.
export function useApiResource(loader, deps = []) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    loader({ signal: ctrl.signal })
      .then((result) => {
        setData(result);
        setLoading(false);
      })
      .catch((err) => {
        if (err.name === "AbortError") return;
        setError(err.message);
        setLoading(false);
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, loading, error, setData };
}
