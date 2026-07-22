import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { SWRConfig, useSWRConfig, type SWRConfiguration } from 'swr';
// next/compat/router (NOT next/router): returns null instead of throwing when
// no RouterContext is mounted — jest renders the provider bare (see Header.tsx).
import { useRouter } from 'next/compat/router';
import { useT } from '@/lib/i18n';

interface RestFailure {
  key: string;
  message: string;
}

function messageFrom(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return String(error || 'Request failed');
}

function ApiErrorBanner({ failure, onDismiss }: { failure: RestFailure; onDismiss: () => void }) {
  const t = useT();
  const { mutate } = useSWRConfig();
  return (
    <div
      role="alert"
      data-testid="api-error-banner"
      className="fixed z-50 left-1/2 top-2 -translate-x-1/2 max-w-[min(92vw,48rem)] rounded border border-terminal-down/70 bg-terminal-bg px-3 py-2 text-xs text-terminal-text shadow-lg"
    >
      <span className="font-semibold text-terminal-down">{t('api.errorTitle')}</span>{' '}
      <span>{failure.message}</span>
      <button
        type="button"
        onClick={() => void mutate(failure.key)}
        className="ml-3 font-semibold text-terminal-accent hover:underline"
      >
        {t('api.retry')}
      </button>
      <button
        type="button"
        onClick={onDismiss}
        aria-label={t('api.dismiss')}
        className="ml-2 text-terminal-muted hover:text-terminal-text"
      >
        ×
      </button>
    </div>
  );
}

/**
 * App-wide read-error surface for SWR-backed REST requests. Individual panels
 * keep their loading/empty states; transport failures no longer masquerade as
 * either one and can be retried from a consistent banner.
 */
export default function ApiStatusProvider({ children }: { children: ReactNode }) {
  const [failure, setFailure] = useState<RestFailure | null>(null);

  // A failure banner belongs to the page that produced it — clear the slot on
  // every route change so it cannot linger across navigations. Null-safe:
  // without a RouterContext (jest) the path stays null and this never fires.
  const router = useRouter();
  const routePath = router?.asPath ?? null;
  useEffect(() => {
    setFailure(null);
  }, [routePath]);

  const onError = useCallback((error: unknown, key: string) => {
    setFailure({ key, message: messageFrom(error) });
  }, []);
  const onSuccess = useCallback((_data: unknown, key: string) => {
    setFailure((current) => (current?.key === key ? null : current));
  }, []);
  const value = useMemo<SWRConfiguration>(() => ({ onError, onSuccess }), [onError, onSuccess]);

  return (
    <SWRConfig value={value}>
      {children}
      {failure && <ApiErrorBanner failure={failure} onDismiss={() => setFailure(null)} />}
    </SWRConfig>
  );
}
