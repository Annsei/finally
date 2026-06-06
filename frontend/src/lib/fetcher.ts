// Shared SWR fetcher — used by Header, WatchlistPanel, and any component
// that fetches REST data. A single definition avoids duplicate inline fetchers.
export const fetcher = (url: string) => fetch(url).then((r) => r.json());
