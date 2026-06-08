import { JetBrains_Mono } from 'next/font/google';
import type { AppProps } from 'next/app';
import '@/styles/globals.css';

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  weight: ['400', '600'],
  variable: '--font-mono',
  display: 'swap',
});

export default function App({ Component, pageProps }: AppProps) {
  return (
    <main className={`${jetbrainsMono.variable} font-mono`}>
      <Component {...pageProps} />
    </main>
  );
}
