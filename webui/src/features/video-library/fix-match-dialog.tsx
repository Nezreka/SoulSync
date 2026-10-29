/**
 * fix match: search TMDB for the title a library movie/show really is and
 * re-point it. the pick is locked server-side, so a scan can't undo it
 * (core/video/identity.py). pasting a bare TMDB id finds exactly that title.
 */

import { useEffect, useRef, useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';

import type { MatchCandidate, TitleKind } from './types';

import { identifyTitle, searchMatches } from './api';
import s from './video-library.module.css';

export function FixMatchDialog({
  kind,
  id,
  title,
  year,
  onClose,
  onFixed,
}: {
  kind: TitleKind;
  id: number;
  title: string;
  year: number | null;
  onClose: () => void;
  onFixed: () => void;
}) {
  const [query, setQuery] = useState(year ? `${title} ${year}` : title);
  const [results, setResults] = useState<MatchCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setResults(null);
      return;
    }
    const mine = ++seq.current;
    const t = window.setTimeout(() => {
      searchMatches(kind, id, q)
        .then((r) => {
          if (mine !== seq.current) return;
          setResults(r);
          setError(null);
          setPicked(null);
        })
        .catch((e: Error) => {
          if (mine === seq.current) setError(e.message || 'Search failed');
        });
    }, 300);
    return () => window.clearTimeout(t);
  }, [kind, id, query]);

  const apply = async () => {
    if (picked == null) return;
    setSaving(true);
    try {
      const res = await identifyTitle(kind, id, picked);
      window.showToast?.(
        `Matched to ${res.title}${res.year ? ` (${res.year})` : ''} — artwork and details refresh shortly`,
        'success',
      );
      onFixed();
    } catch (e) {
      setSaving(false);
      window.showToast?.((e as Error).message || 'Couldn’t fix the match', 'error');
    }
  };

  return (
    <DialogFrame
      open
      onOpenChange={(open) => !open && onClose()}
      aria-label="Fix match"
      className={s.dialog}
    >
      <DialogHeader title="Fix match">
        <span className={s.muted}>Which title is “{title}” really?</span>
      </DialogHeader>
      <DialogBody>
        <input
          className={s.search}
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Title, year, or a TMDB id"
          aria-label="Search TMDB"
          autoFocus
        />
        {error ? <p className={s.empty}>{error}</p> : null}
        {results && !results.length ? (
          <p className={s.empty}>
            Nothing on TMDB by that name. Try fewer words, or paste the TMDB id.
          </p>
        ) : null}
        <ul className={s.matches} role="listbox" aria-label="TMDB results">
          {(results ?? []).map((r) => (
            <li key={r.id}>
              <button
                type="button"
                role="option"
                aria-selected={picked === r.id}
                className={s.match}
                data-picked={picked === r.id ? 'yes' : 'no'}
                onClick={() => setPicked(r.id)}
              >
                {r.poster_url ? (
                  <img className={s.matchPoster} src={r.poster_url} alt="" loading="lazy" />
                ) : (
                  <span className={s.matchPoster} aria-hidden="true" />
                )}
                <span className={s.matchText}>
                  <span className={s.matchTitle}>
                    {r.title}
                    {r.year ? <span className={s.muted}> ({r.year})</span> : null}
                  </span>
                  <span className={s.matchOverview}>{r.overview || 'No overview.'}</span>
                  <span className={s.muted}>TMDB {r.id}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </DialogBody>
      <DialogFooter>
        <p className={s.note}>The pick is locked, so a library scan can’t switch it back.</p>
        <button type="button" className={s.linkBtn} onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className={s.btnPrimary}
          disabled={picked == null || saving}
          onClick={() => void apply()}
        >
          {saving ? 'Matching…' : 'Use this title'}
        </button>
      </DialogFooter>
    </DialogFrame>
  );
}
