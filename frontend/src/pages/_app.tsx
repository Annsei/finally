import { useEffect } from 'react';
import type { AppProps } from 'next/app';
import '@/styles/globals.css';
import { useMarketProfile, applyMarketAttr } from '@/lib/marketProfile';
import { usePriceStream } from '@/hooks/usePriceStream';

// Font note: we intentionally do NOT use next/font/google — it downloads the
// font at build time, which makes builds fail on flaky networks (observed in
// CI and docker builds). tailwind.config.js's font-mono stack prefers a
// locally installed JetBrains Mono and falls back to system monospace fonts.

// Runtime market wiring (FinAlly-CN, CN-3 §1): stamp <html data-market="…">
// from the profile so the CSS-variable colour flip engages. US (the default)
// resolves to data-market="us", which is the CSS default — no visual change.
function MarketProfileEffect() {
  const profile = useMarketProfile();
  useEffect(() => {
    applyMarketAttr(profile.market);
  }, [profile.market]);
  return null;
}

// P1 §2: the single app-wide SSE connection lives at the _app level so
// client-side navigation between pages never drops the stream. Pages must NOT
// call usePriceStream() themselves.
function PriceStreamEffect() {
  usePriceStream();
  return null;
}

export default function App({ Component, pageProps }: AppProps) {
  return (
    <main className="font-mono">
      <MarketProfileEffect />
      <PriceStreamEffect />
      <Component {...pageProps} />
    </main>
  );
}
