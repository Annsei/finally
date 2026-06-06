---
phase: 03-frontend-foundation
reviewed: 2026-06-06T00:00:00Z
depth: standard
files_reviewed: 18
files_reviewed_list:
  - frontend/src/components/SparklineChart.tsx
  - frontend/src/components/WatchlistRow.tsx
  - frontend/src/components/WatchlistPanel.tsx
  - frontend/src/pages/index.tsx
  - frontend/src/stores/priceStore.ts
  - frontend/src/hooks/usePriceStream.ts
  - frontend/src/components/Header.tsx
  - frontend/src/lib/fetcher.ts
  - frontend/src/types/market.ts
  - frontend/src/pages/_app.tsx
  - frontend/src/styles/globals.css
  - frontend/jest.config.js
  - frontend/__mocks__/lightweightChartsStub.js
  - frontend/__tests__/SparklineChart.test.tsx
  - frontend/__tests__/WatchlistRow.test.tsx
  - frontend/__tests__/WatchlistPanel.test.tsx
  - frontend/__tests__/index.test.tsx
  - frontend/__tests__/Header.test.tsx
findings:
  critical: 0
  warning: 5
  info: 3
  total: 8
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-06-06
**Depth:** standard
**Files Reviewed:** 18
**Status:** issues_found

## Summary

The Phase 3 frontend-foundation implements the SSE price store, a watchlist panel with sparklines, a header with connection status, and supporting tests. The architecture is sound: Zustand atom selectors avoid re-render storms, EventSource lifecycle is properly cleaned up, and the test suite passes 31/31 tests. No security vulnerabilities or data-loss risks were identified.

Five warnings require attention before Phase 4 builds on this foundation. The most impactful are: (1) a stuck flash state caused by the `flat` direction early-return cancelling a pending timeout without cleaning the DOM class, (2) the flash animation CSS producing a 500ms fade-in rather than the specified instant-highlight-then-fade behavior, and (3) the shared `fetcher` not throwing on HTTP error responses, causing SWR to treat 4xx/5xx as valid data.

---

## Warnings

### WR-01: Stuck flash class when a `flat` tick interrupts an active flash

**File:** `frontend/src/components/WatchlistRow.tsx:17-36`

**Issue:** The `useEffect` returns a cleanup that calls `clearTimeout(flashTimeoutRef.current)`. When a new SSE tick arrives with `direction === 'flat'`, React executes the previous effect's cleanup (cancelling the scheduled class-removal timeout) and then runs the new effect body which exits at line 19 (`if (priceUpdate.direction === 'flat') return`) without removing the flash class. Result: `flash-up` or `flash-down` remains on the price cell indefinitely — until the next non-flat tick.

Reproduction sequence:
1. Tick arrives with `direction='up'` — `flash-up` class added, 500ms timeout starts.
2. Before 500ms elapses, another tick arrives with `direction='flat'`.
3. Cleanup of effect #1 fires: `clearTimeout` cancels the scheduled class removal.
4. Effect #2 body runs: hits the early return, class stays on element.

**Fix:** Remove any existing flash class regardless of direction at the start of the effect, before the early return:

```tsx
useEffect(() => {
  if (!priceUpdate || !priceRef.current) return;

  const cell = priceRef.current;
  // Always cancel pending timeout and clear classes — even for flat ticks.
  if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
  cell.classList.remove('flash-up', 'flash-down');

  if (priceUpdate.direction === 'flat') return;

  void cell.offsetWidth; // force reflow
  const cls = priceUpdate.direction === 'up' ? 'flash-up' : 'flash-down';
  cell.classList.add(cls);

  flashTimeoutRef.current = setTimeout(() => {
    cell.classList.remove(cls);
  }, 500);

  return () => {
    if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
  };
}, [priceUpdate?.direction, priceUpdate?.timestamp]);
```

---

### WR-02: Flash animation produces a fade-in, not the specified instant-flash-then-fade

**File:** `frontend/src/styles/globals.css:7-15`

**Issue:** The `.flash-up` / `.flash-down` CSS classes use `transition: background-color 500ms ease-out`. When the class is added (after a forced reflow), CSS transitions fire on the property change from `transparent` to `rgba(green, 0.25)` — producing a 500ms fade-in. PLAN.md specifies: "brief green/red background highlight on price change, fading over ~500ms" (instant on, then fade). The current CSS does the opposite.

Additionally, the `transition` rule lives inside `.flash-up` itself. When the class is removed, the transition rule is simultaneously removed; the browser may or may not continue animating the fade-out, making removal behavior implementation-dependent.

Notably, `tailwind.config.js` already defines correct keyframe animations (`flashUp`/`flashDown`: 0% = green, 100% = transparent) exposed as `animate-flash-up` / `animate-flash-down`, but these are unused.

**Fix (Option A — use the already-defined Tailwind keyframe classes):**

In `globals.css`, replace the transition-based classes with keyframe-based classes that animate forward and hold at transparent:

```css
/* Remove these rules from globals.css entirely — use Tailwind animate-flash-* instead */
```

In `WatchlistRow.tsx`, replace `flash-up`/`flash-down` with `animate-flash-up`/`animate-flash-down` and remove the `setTimeout` class-removal (not needed with `forwards` fill):

```tsx
const cls = priceUpdate.direction === 'up' ? 'animate-flash-up' : 'animate-flash-down';
cell.classList.add(cls);
// Remove class after animation completes so it can re-trigger on next tick
flashTimeoutRef.current = setTimeout(() => {
  cell.classList.remove(cls);
}, 500);
```

**Fix (Option B — fix globals.css to use keyframes directly):**

```css
.flash-up {
  animation: flashUp 500ms ease-out forwards;
}
.flash-down {
  animation: flashDown 500ms ease-out forwards;
}
```

And add the matching `@keyframes` rules to `globals.css` (mirrors what is in `tailwind.config.js`).

---

### WR-03: `fetcher` does not throw on HTTP error responses — SWR never enters error state

**File:** `frontend/src/lib/fetcher.ts:3`

**Issue:** `fetch(url).then((r) => r.json())` does not check `r.ok`. When the backend returns a 4xx or 5xx response, `fetch()` resolves (it only rejects on network failure). The error response body is parsed as JSON and handed to SWR as `data`, not as an error. Consequences:

- `Header`: receives an object without `cash`/`total_value` fields → renders `'—'` silently (masked but not surfaced).
- `WatchlistPanel`: `data?.tickers` is `undefined` → `tickers = []` → shows "No prices yet" even when the backend is running but erroring — the user cannot distinguish a loading state from a backend error.
- SWR retry logic does not activate because no error was thrown.

**Fix:**

```ts
export const fetcher = (url: string) =>
  fetch(url).then((r) => {
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  });
```

---

### WR-04: Sparkline loses every other data point due to `Math.floor` timestamp coercion

**File:** `frontend/src/components/SparklineChart.tsx:61`

**Issue:** `Math.floor(priceUpdate.timestamp)` coerces the Unix float timestamp (e.g., `1717700000.5`) to integer seconds. Since the backend simulator emits prices at 500ms intervals, two consecutive ticks frequently share the same integer second (e.g., `1717700000.1` and `1717700000.6` both become `1717700000`). `lightweight-charts` treats `series.update()` calls with the same time value as updates to the same point — silently overwriting it rather than adding a new data point. This means the sparkline accumulates at most one data point per second instead of two, halving the visual fidelity of the chart.

Note: lightweight-charts does NOT throw on equal timestamps — it only throws when the new time is strictly less than the last time (confirmed by source inspection at line 11968 of the standalone bundle). This is a data fidelity issue, not a crash.

**Fix:** Use a monotonically incrementing local counter as the time axis for sparklines (since sparklines only need relative ordering, not wall-clock accuracy), or multiply the float timestamp to milliseconds and use `BusinessDay` mode:

```tsx
// Option A: monotonic counter (simpler, correct for sparkline use case)
const tickCountRef = useRef<number>(0);
// ...in the update effect:
tickCountRef.current += 1;
seriesRef.current.update({
  time: tickCountRef.current as UTCTimestamp,
  value: priceUpdate.price,
});
```

---

### WR-05: `SparklineChart` mount effect silently ignores `width`/`height` prop changes

**File:** `frontend/src/components/SparklineChart.tsx:20-55`

**Issue:** The mount effect that calls `createChart(containerRef.current, { width, height })` has an empty dependency array (`[]`) with `eslint-disable-line react-hooks/exhaustive-deps`. If `width` or `height` props change after initial render (e.g., a parent resizes or passes different dimensions), the chart retains its original dimensions. The `div` container element's inline style updates correctly (line 66), but the canvas inside the chart does not resize.

In Phase 3, `WatchlistRow` always passes `width={80} height={28}` (fixed), so this is not a runtime bug today. However, the incorrect dependency array makes the component fragile for any future use with dynamic dimensions.

**Fix:** Either add `width` and `height` to the effect dependency array (which will destroy and recreate the chart on resize — acceptable for a sparkline), or call `chart.applyOptions({ width, height })` in a separate effect:

```tsx
// Separate resize effect
useEffect(() => {
  chartRef.current?.applyOptions({ width, height });
}, [width, height]);
```

---

## Info

### IN-01: `Header.fmt()` renders `'NaN'` instead of `'—'` for non-numeric values

**File:** `frontend/src/components/Header.tsx:41-43`

**Issue:** `fmt(n)` guards only against `undefined` with `n !== undefined`. If `data.cash` or `data.total_value` arrive as `NaN` (e.g., backend sends `null` for these fields, which passes TypeScript's type guard at runtime), `NaN.toLocaleString()` returns the string `'NaN'` and renders in the UI instead of the dash placeholder.

**Fix:**

```ts
const fmt = (n: number | undefined) =>
  n != null && isFinite(n)
    ? n.toLocaleString('en-US', { minimumFractionDigits: 2 })
    : '—';
```

---

### IN-02: `WatchlistPanel` conflates loading, error, and genuinely-empty states

**File:** `frontend/src/components/WatchlistPanel.tsx:15-22`

**Issue:** `const tickers = data?.tickers?.map((t) => t.ticker) ?? [];` produces an empty array for three distinct situations: (a) data not yet loaded (SWR pending), (b) SWR error (backend returned non-OK status — especially after WR-03 is fixed), and (c) a genuinely empty watchlist. All three display "No prices yet", which is incorrect messaging for cases (b) and (c).

**Fix:** Destructure SWR's `isLoading` and `error` fields to render distinct states:

```tsx
const { data, isLoading, error } = useSWR<WatchlistResponse>('/api/watchlist', fetcher);
const tickers = data?.tickers?.map((t) => t.ticker) ?? [];

if (isLoading) return <div>Loading watchlist…</div>;
if (error) return <div>Failed to load watchlist</div>;
if (tickers.length === 0) return <div>Watchlist is empty</div>;
```

---

### IN-03: Tailwind `animate-flash-up` / `animate-flash-down` keyframe definitions are dead code

**File:** `frontend/tailwind.config.js:27-39`

**Issue:** `tailwind.config.js` defines `keyframes.flashUp`, `keyframes.flashDown`, and `animation['flash-up']`/`animation['flash-down']`. None of these are referenced anywhere in the source files (`frontend/src/`). The component uses plain `flash-up`/`flash-down` CSS classes from `globals.css` instead. These Tailwind utility classes cannot be purged by Tailwind's content scanner because they are never referenced as class strings.

This is noted together with WR-02 since the fix for WR-02 (switching to keyframe-based animation) would consume these definitions and remove the dead-code smell.

**Fix:** If WR-02 is addressed by switching to Tailwind keyframe classes, this finding resolves automatically. If not, remove the unused keyframe/animation definitions from `tailwind.config.js`.

---

_Reviewed: 2026-06-06_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
