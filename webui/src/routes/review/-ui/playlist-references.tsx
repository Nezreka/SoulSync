import { useEffect, useState } from 'react';

import { Button } from '@/components/form/form';

import styles from './review-page.module.css';

type Reference = { id: string; title: string; server?: string; url?: string };

export function PlaylistReferences({
  caseId,
  path,
  names = [],
  removal = false,
  lastError,
  onRecheck,
}: {
  caseId?: string;
  path?: string | null;
  names?: string[];
  removal?: boolean;
  lastError?: string | null;
  onRecheck?: (cleared: boolean) => void;
}) {
  const [references, setReferences] = useState<Reference[]>(
    names.map((title) => ({ id: title, title })),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [checked, setChecked] = useState(false);
  useEffect(() => {
    if (!caseId || !path) return;
    let live = true;
    setChecked(false);
    setError('');
    setReferences(names.map((title) => ({ id: title, title })));
    void fetch(
      `/api/repair/review-queue/${encodeURIComponent(caseId)}/playlist-references?${new URLSearchParams({ path })}`,
    )
      .then(async (response) => {
        if (!response.ok) throw new Error('Playlist references could not be checked.');
        return response.json();
      })
      .then((result: { playlists: Reference[] }) => {
        if (!Array.isArray(result.playlists)) throw new Error('Invalid playlist response');
        if (live) {
          setReferences(result.playlists);
          setChecked(true);
        }
      })
      .catch(() => {
        if (live && (removal || lastError)) setError('Playlist references could not be checked.');
      });
    return () => {
      live = false;
    };
  }, [caseId, path, removal, lastError]);
  const recheck = async () => {
    if (!caseId || !path) return;
    setBusy(true);
    setError('');
    try {
      const response = await fetch(
        `/api/repair/review-queue/${encodeURIComponent(caseId)}/playlist-references`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path }),
        },
      );
      const result = await response.json();
      if (!response.ok)
        throw new Error(result.error || 'Playlist references could not be checked.');
      if (!Array.isArray(result.playlists))
        throw new Error('Playlist references could not be checked.');
      setReferences(result.playlists);
      setChecked(true);
      onRecheck?.(result.playlists.length === 0);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (lastError && !lastError.toLowerCase().includes('playlist'))
    return (
      <section className={styles.problemCard} data-tone="warning" aria-label="Saved action blocked">
        <h4>Saved action needs attention</h4>
        <p>{lastError}</p>
        <p>Your choice is saved. Review the issue before retrying the album plan.</p>
      </section>
    );
  if (!references.length && !lastError && !error && !(removal && checked)) return null;
  return (
    <section
      className={`${styles.problemCard} ${styles.playlistReferences}`}
      data-tone={lastError || (removal && references.length) ? 'warning' : 'info'}
      aria-label="Playlist references"
    >
      <h4>
        {removal && references.length
          ? 'Removal blocked by playlist references'
          : references.length
            ? `Used in ${references.length} playlist${references.length === 1 ? '' : 's'}`
            : lastError
              ? 'Saved action needs attention'
              : 'Playlist references checked'}
      </h4>
      {references.length > 0 && (
        <>
          <p>
            {removal
              ? 'Your removal choice is saved. Update these playlists, then recheck references before applying.'
              : 'These playlists use this copy.'}
          </p>
          <ul>
            {references.map((entry) => (
              <li key={entry.id}>
                {entry.url ? (
                  <a href={entry.url} target="_blank" rel="noreferrer">
                    {entry.title} ↗
                  </a>
                ) : (
                  entry.title
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {checked && !references.length && <p>No playlist references remain for this copy.</p>}
      {lastError && (
        <details>
          <summary>Previous attempt</summary>
          <p>{lastError}</p>
        </details>
      )}
      {error && <p role="alert">{error}</p>}
      {caseId && path && (
        <Button disabled={busy} onClick={() => void recheck()}>
          {busy ? 'Checking…' : 'Recheck references'}
        </Button>
      )}
    </section>
  );
}
