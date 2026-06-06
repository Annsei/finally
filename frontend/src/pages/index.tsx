import Head from 'next/head';

export default function Home() {
  return (
    <>
      <Head>
        <title>FinAlly — AI Trading Workstation</title>
        <meta name="description" content="AI-powered trading workstation" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="/favicon.ico" />
      </Head>
      <div className="min-h-screen bg-terminal-bg text-terminal-text font-mono">
        <p className="p-4 text-terminal-muted">FinAlly loading...</p>
      </div>
    </>
  );
}
