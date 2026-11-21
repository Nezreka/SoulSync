/**
 * the ask for a title you don't own, through soulsync's own request system:
 * a profile that may download gets it straight away, one that asks files a
 * request for whoever approves, and one that may do neither sees nothing.
 * every rule (quotas, ratings, qualities) is the server's, not ours.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { requestOptionsQuery, requestTitle, type RequestResult } from '../-player.api';
import { styles as s } from './kit';

export function RequestButton({
  kind,
  tmdbId,
  title,
  year,
  poster,
}: {
  kind: 'movie' | 'show';
  tmdbId: number;
  title: string;
  year?: number | string | null;
  poster?: string | null;
}) {
  const options = useQuery(requestOptionsQuery());
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RequestResult | null>(null);
  const mode = options.data?.[kind] ?? 'none';
  if (mode === 'none') return null;

  const done = result && result.state !== 'error';
  const label = busy
    ? mode === 'download'
      ? 'Adding…'
      : 'Asking…'
    : result?.state === 'got'
      ? 'Added, it will download'
      : result?.state === 'asked'
        ? result.already
          ? 'Already requested'
          : 'Requested'
        : result?.state === 'on-the-way'
          ? 'Already on the way'
          : result?.state === 'owned'
            ? 'Already in your library'
            : mode === 'download'
              ? 'Get it'
              : 'Request';

  return (
    <span className={s.requestWrap}>
      <button
        type="button"
        className={s.btnPrimary}
        disabled={busy || !!done}
        onClick={async () => {
          setBusy(true);
          const r = await requestTitle({ kind, tmdb_id: tmdbId, title, year, poster_url: poster });
          setBusy(false);
          setResult(r);
          if (r.state === 'got' || r.state === 'owned')
            void queryClient.invalidateQueries({ queryKey: ['player', 'video-catalog'] });
        }}
        data-nav
      >
        {label}
      </button>
      {result?.state === 'error' ? (
        <span className={s.requestError} role="alert">
          {result.message}
        </span>
      ) : mode === 'ask' && !result ? (
        <span className={s.notYet}>Sent to whoever approves requests</span>
      ) : null}
    </span>
  );
}
