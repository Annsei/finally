export const fetcher = (url: string) =>
  fetch(url).then((r) => {
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  });
