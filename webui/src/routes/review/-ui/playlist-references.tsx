import { useEffect, useState } from 'react';

import { useConfirmDialog } from '@/components/dialog/use-confirm-dialog';
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
  findingId,
  evidenceToken,
  overrideSaved = false,
}: {
  caseId?: string;
  path?: string | null;
  names?: string[];
  removal?: boolean;
  lastError?: string | null;
  onRecheck?: (cleared: boolean) => void;
  findingId?: number;
  evidenceToken?: string;
  overrideSaved?: boolean;
}) {
  const { confirm, dialog } = useConfirmDialog();
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
  const override = async () => {
    const accepted = await confirm({
      title: 'Remove this file despite playlist references?',
      destructive: true,
      confirmText: 'Save removal override',
      message: (
        <div className={styles.confirmationCopy}>
          <p>Applying this removal will leave unavailable entries in these playlists:</p>
          <ul>
            {references.map((entry) => (
              <li key={entry.id}>{entry.title}</li>
            ))}
          </ul>
          <p>
            The file will go to recoverable quarantine. SoulSync will not edit the playlists. This
            acknowledgement applies only to this saved file action.
          </p>
        </div>
      ),
    });
    if (!accepted || !caseId || findingId == null) return;
    setBusy(true);
    setError('');
    try {
      const response = await fetch(
        `/api/repair/review-queue/${encodeURIComponent(caseId)}/findings/${findingId}/playlist-override`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            evidence_token: evidenceToken,
            acknowledge_broken_references: true,
            playlist_ids: references.map((entry) => entry.id),
          }),
        },
      );
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not save the override');
      onRecheck?.(true);
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
        {overrideSaved
          ? 'Playlist removal override saved'
          : removal && references.length
            ? 'Removal blocked by playlist references'
            : references.length
              ? `Used in ${references.length} playlist${references.length === 1 ? '' : 's'}`
              : lastError
                ? 'Saved action needs attention'
                : 'Playlist references checked'}
      </h4>
      {references.length > 0 && (
        <>
          {removal && (
            <p>
              {overrideSaved
                ? 'Applying this removal may leave unavailable playlist entries.'
                : removal
                  ? 'Update these playlists and recheck references, or explicitly override this restriction for the saved removal.'
                  : 'These playlists use this copy.'}
            </p>
          )}
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
      <div className={styles.playlistActions}>
        {caseId && path && (
          <Button disabled={busy} onClick={() => void recheck()}>
            {busy ? 'Checking…' : 'Recheck references'}
          </Button>
        )}
        {removal && references.length > 0 && findingId != null && !overrideSaved && (
          <Button disabled={busy || !checked} onClick={() => void override()}>
            Remove anyway…
          </Button>
        )}
      </div>
      {dialog}
    </section>
  );
}
