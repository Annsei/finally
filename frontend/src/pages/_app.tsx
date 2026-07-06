import type { AppProps } from 'next/app';
import '@/styles/globals.css';

// Font note: we intentionally do NOT use next/font/google — it downloads the
// font at build time, which makes builds fail on flaky networks (observed in
// CI and docker builds). tailwind.config.js's font-mono stack prefers a
// locally installed JetBrains Mono and falls back to system monospace fonts.
export default function App({ Component, pageProps }: AppProps) {
  return (
    <main className="font-mono">
      <Component {...pageProps} />
    </main>
  );
}
