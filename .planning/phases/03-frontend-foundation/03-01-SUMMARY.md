---
phase: 03-frontend-foundation
plan: "01"
subsystem: frontend
tags: [next.js, tailwind, typescript, jest, types, scaffold]
dependency_graph:
  requires: []
  provides:
    - frontend/next.config.js
    - frontend/tailwind.config.js
    - frontend/src/styles/globals.css
    - frontend/src/pages/_app.tsx
    - frontend/src/types/market.ts
    - frontend/jest.config.js
    - frontend/jest.setup.ts
    - frontend/__mocks__/nextFontMock.js
  affects:
    - All Phase 3 frontend plans (build on this scaffold)
    - Dockerfile (Node stage copies frontend/ and runs npm build)
tech_stack:
  added:
    - next@16.2.7 (Pages Router, static export)
    - react@19.2.4
    - react-dom@19.2.4
    - typescript@^5
    - tailwindcss@^3.4.19
    - postcss@^8.5.15
    - autoprefixer@^10.5.0
    - lightweight-charts@^5.2.0
    - zustand@^5.0.14
    - swr@^2.4.1
    - jest@^30.4.2
    - jest-environment-jsdom@^30.4.1
    - jest-canvas-mock@^2.5.2
    - "@testing-library/react@^16.3.2"
    - "@testing-library/dom@^10.4.1"
    - "@testing-library/jest-dom@^6.9.1"
    - ts-node@^10.9.2
    - "@types/jest@^30.0.0"
  patterns:
    - Next.js Pages Router with static export (output: 'export')
    - Tailwind v3 terminal color tokens via theme.extend.colors
    - next/font/google JetBrains Mono with CSS variable --font-mono
    - CSS class-toggle flash animation (.flash-up / .flash-down)
    - next/jest createJestConfig with jsdom + canvas mock
    - Snake_case TypeScript types mirroring backend to_dict() contract
key_files:
  created:
    - frontend/next.config.js
    - frontend/tailwind.config.js
    - frontend/postcss.config.js
    - frontend/src/styles/globals.css
    - frontend/src/pages/_app.tsx
    - frontend/src/pages/index.tsx
    - frontend/src/types/market.ts
    - frontend/jest.config.js
    - frontend/jest.setup.ts
    - frontend/__mocks__/nextFontMock.js
    - frontend/__tests__/smoke.test.ts
  modified:
    - frontend/package.json (added test script, all deps)
    - frontend/package-lock.json
decisions:
  - "Used next.config.js (CommonJS module.exports) not next.config.ts — avoids Pitfall 6 ESM/CJS mismatch with output: 'export'"
  - "Used --no-tailwind scaffold flag then installed tailwindcss@^3 manually — avoids Pitfall 7 where create-next-app@16 scaffolds Tailwind v4"
  - "Deleted create-next-app generated next.config.ts and replaced with next.config.js for CommonJS compatibility"
  - "Replaced default index.tsx (used next/image + module CSS) with minimal terminal-themed placeholder using Tailwind classes"
  - "Removed Home.module.css (unused after index.tsx replacement)"
metrics:
  duration: "~15 minutes"
  completed: "2026-06-06"
  tasks_completed: 2
  tasks_total: 3
  files_created: 11
  files_modified: 2
---

# Phase 03 Plan 01: Frontend Foundation Scaffold Summary

**One-liner:** Next.js 16 Pages Router scaffolded with static export, Tailwind v3 terminal theme (11 color tokens), JetBrains Mono font, snake_case market types mirroring backend, and Jest 30 + jsdom + canvas mock test infrastructure.

## What Was Built

### Task 1 (Pre-approved by user)
Package legitimacy checkpoint approved. All npm packages confirmed legitimate before install.

### Task 2: Next.js Scaffold + Tailwind v3 + Theme
- Scaffolded `frontend/` with `create-next-app@latest` using Pages Router (`--no-app`), TypeScript, ESLint, no built-in Tailwind (`--no-tailwind`)
- Created `frontend/next.config.js` (CommonJS) with `output: 'export'` and `images: { unoptimized: true }`
- Installed Tailwind v3 (`tailwindcss@^3.4.19`) manually via `npm install -D tailwindcss@^3 postcss autoprefixer` + `npx tailwindcss init -p`
- Wrote `frontend/tailwind.config.js` with full terminal color palette: bg `#0d1117`, surface `#1a1a2e`, border `#30363d`, text `#e6edf3`, muted `#8b949e`, accent `#ecad0a`, blue `#209dd7`, purple `#753991`, up `#22c55e`, down `#ef4444`, amber `#f59e0b`; keyframes `flashUp`/`flashDown`; custom mono font family via `var(--font-mono)`
- Wrote `frontend/src/styles/globals.css` with `@tailwind` directives and `.flash-up` / `.flash-down` classes with `transition: background-color 500ms ease-out`
- Wrote `frontend/src/pages/_app.tsx` importing `JetBrains_Mono` from `next/font/google` with `variable: '--font-mono'`, `weight: ['400','600']`, `display: 'swap'`
- Installed production deps: `lightweight-charts`, `zustand`, `swr`
- Build verified: `npm run build` exits 0, `frontend/out/index.html` produced

### Task 3: Market Types + Jest Infrastructure
- Created `frontend/src/types/market.ts` exporting `PriceUpdate` (7 snake_case fields: ticker, price, previous_price, timestamp, change, change_percent, direction), `PriceMap`, `WatchlistEntry`, `WatchlistResponse`, `Position`, `PortfolioResponse`, `DEFAULT_TICKERS` — all matching backend `to_dict()` contract exactly
- Created `frontend/jest.config.js` using `next/jest` `createJestConfig`: `testEnvironment: 'jsdom'`, `moduleNameMapper` for `@/*` alias and `next/font/google` → mock
- Created `frontend/jest.setup.ts` importing `@testing-library/jest-dom` and `jest-canvas-mock`
- Created `frontend/__mocks__/nextFontMock.js` exporting `JetBrains_Mono: () => ({ variable: '--font-mono', className: 'mock-font' })`
- Created `frontend/__tests__/smoke.test.ts` placeholder (passes immediately)
- Added `"test": "jest"` script to `package.json`
- Verified: `npx tsc --noEmit` exits 0, `npm test -- --watchAll=false` exits 0 (1 test passed)

## Verification Results

| Check | Result |
|-------|--------|
| `next.config.js` has `output: 'export'` + `module.exports` | PASS |
| `tailwindcss` version is `^3.x` in package.json | PASS |
| tailwind.config.js contains all locked hex tokens | PASS |
| `globals.css` has `.flash-up` / `.flash-down` with `transition` | PASS |
| `_app.tsx` imports `JetBrains_Mono` and `@/styles/globals.css` | PASS |
| No `src/app/` directory (Pages Router only) | PASS |
| `npm run build` exits 0 + `out/index.html` exists | PASS |
| `market.ts` exports all 5 types + DEFAULT_TICKERS | PASS |
| `market.ts` uses snake_case (previous_price, avg_cost, etc.) | PASS |
| `jest.config.js` maps `next/font/google` to mock + jsdom | PASS |
| `jest.setup.ts` imports `jest-canvas-mock` | PASS |
| `npx tsc --noEmit` exits 0 | PASS |
| `npm test -- --watchAll=false` exits 0 | PASS |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] create-next-app generated next.config.ts instead of next.config.js**
- **Found during:** Task 2
- **Issue:** Recent `create-next-app@16` generates `next.config.ts` (TypeScript ESM) rather than `next.config.js` (CommonJS). The plan explicitly requires CommonJS `module.exports` form (Pitfall 6).
- **Fix:** Deleted `next.config.ts`, created `next.config.js` with `module.exports = nextConfig` pattern
- **Files modified:** `frontend/next.config.js` (created), `frontend/next.config.ts` (deleted)
- **Commit:** 521cd17

**2. [Rule 1 - Bug] Default index.tsx used next/image and module CSS incompatible with the scaffold**
- **Found during:** Task 2
- **Issue:** The scaffolded `src/pages/index.tsx` imported `next/image` with non-URL `src` props and referenced `Home.module.css`, neither of which fits the terminal theme or plan requirements. The `--no-tailwind` scaffold produces a plain default page.
- **Fix:** Replaced `index.tsx` with a minimal terminal-themed placeholder using `bg-terminal-bg` Tailwind class; deleted `Home.module.css`
- **Files modified:** `frontend/src/pages/index.tsx`, deleted `frontend/src/styles/Home.module.css`
- **Commit:** 521cd17

## Known Stubs

- `frontend/src/pages/index.tsx` — renders a minimal "FinAlly loading..." placeholder. The full dashboard (Header, WatchlistPanel, SSE hook) is implemented by Plans 02–04 in Phase 3.
- `frontend/__tests__/smoke.test.ts` — single `expect(true).toBe(true)` placeholder. Replaced by component tests in downstream plans (FE-02 through FE-08).
- `frontend/src/pages/api/hello.ts` — default Next.js API stub from scaffold, unused. Will be removed or ignored; backend API is served by FastAPI.

## Threat Flags

No new security surface introduced. The scaffolded `frontend/src/pages/api/hello.ts` is a static-export-incompatible API stub that will not appear in `out/` (Next.js ignores API routes during static export). No network endpoints, auth paths, or schema changes were introduced.

## Self-Check: PASSED

All created files verified on disk. Both task commits confirmed in git log:
- `521cd17` — feat(03-01): scaffold Next.js Pages Router with static export and Tailwind v3
- `5014ce1` — feat(03-01): define market types and Jest test infrastructure
