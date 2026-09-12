import { useEffect, useState } from 'react';

import type { AudiobookLibraryEntry, AudiobookMatchResults } from '../-audiobooks.types';

import { fetchLibraryMatches, saveLibraryMatch } from '../-audiobooks.api';
import styles from './audiobook-library.module.css';

export function AudiobookLibraryMatch({
  book,
  onBack,
  onSaved,
}: {
  book: AudiobookLibraryEntry;
  onBack: () => void;
  onSaved: () => void;
}) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<AudiobookMatchResults>({
    candidates: book.match_candidates || [],
    scan_signature: book.scan_signature || '',
    match_revision: book.match_revision || 0,
  });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [searched, setSearched] = useState(Boolean(book.match_candidates?.length));

  useEffect(() => {
    setResults({
      candidates: book.match_candidates || [],
      scan_signature: book.scan_signature || '',
      match_revision: book.match_revision || 0,
    });
  }, [book]);

  const search = async () => {
    setLoading(true);
    setError('');
    try {
      setResults(await fetchLibraryMatches(book.asin, query));
      setSearched(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not search the catalogue.');
    } finally {
      setLoading(false);
    }
  };
  const save = async (action: 'confirm' | 'ignore' | 'retry', asin = '') => {
    setSaving(true);
    setError('');
    try {
      await saveLibraryMatch(book.asin, results, action, asin);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the match.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={styles.matchPane}>
      <button type="button" className={styles.back} onClick={onBack}>
        ← Back to library
      </button>
      <h3>Match this recording</h3>
      <div className={styles.localEvidence}>
        <strong>{book.title}</strong>
        <p>
          {book.author || 'Author unknown'} ·{' '}
          {book.narrator ? `Narrated by ${book.narrator}` : 'Narrator unknown'} ·{' '}
          {book.runtime_minutes ? `${book.runtime_minutes} minutes` : 'Runtime unknown'}
        </p>
        <p>
          {book.file_count} files · {book.grouping || book.path}
        </p>
        {book.catalog_asin && <p>Saved catalogue edition: {book.catalog_asin}</p>}
        {book.match_evidence?.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </div>
      <p>
        Compare the narrator, runtime and edition before confirming. Your choice changes the
        catalogue link and ownership badges; it leaves your files and download origin intact.
      </p>
      <form
        className={styles.matchSearch}
        onSubmit={(e) => {
          e.preventDefault();
          void search();
        }}
      >
        <input
          aria-label="Search catalogue or enter ASIN"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={`${book.title} — or paste an Audible ASIN`}
        />
        <button type="submit" className={styles.primary} disabled={loading || saving}>
          {loading ? 'Searching…' : 'Find editions'}
        </button>
      </form>
      {error && (
        <p role="alert" className={styles.notice}>
          {error}
        </p>
      )}
      {!searched && <p>Use Find editions to search, or keep this book unmatched.</p>}
      {searched && !loading && results.candidates.length === 0 && (
        <p>No candidates found. Try a shorter title, the author’s name, or an ASIN.</p>
      )}
      <ul className={styles.candidates}>
        {results.candidates.map((candidate) => (
          <li key={candidate.book.asin}>
            {candidate.book.cover_url && (
              <img src={candidate.book.cover_url} alt="" loading="lazy" />
            )}
            <div>
              <h4>{candidate.book.title}</h4>
              <p>{candidate.book.author_names?.join(', ') || 'Author unavailable'}</p>
              <p>
                {candidate.book.narrator_names?.join(', ') || 'Narrator unavailable'} ·{' '}
                {candidate.book.runtime_minutes || '?'} min ·{' '}
                {candidate.book.language || 'Language unknown'} ·{' '}
                {candidate.book.format_type || 'Edition unknown'}
              </p>
              <p className={styles.matchScore}>
                Match score {candidate.score}/100 · {candidate.book.asin}
              </p>
              <ul>
                {candidate.evidence.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
              {candidate.conflicts.length > 0 && (
                <p className={styles.conflict}>{candidate.conflicts.join(' · ')}</p>
              )}
            </div>
            <button
              type="button"
              className={styles.primary}
              disabled={saving || loading}
              onClick={() => void save('confirm', candidate.book.asin)}
            >
              Use this edition
            </button>
          </li>
        ))}
      </ul>
      <div className={styles.matchActions}>
        <button type="button" disabled={saving || loading} onClick={() => void save('ignore')}>
          Keep unmatched
        </button>
        <button type="button" disabled={saving || loading} onClick={() => void save('retry')}>
          Queue automatic matching
        </button>
        <small>
          Queued books are checked on the next scan. Keeping a book unmatched prevents automatic
          matching until you change it.
        </small>
      </div>
    </div>
  );
}
