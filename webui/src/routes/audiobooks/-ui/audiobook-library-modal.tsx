import { Link } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState } from 'react';

import type { AudiobookLibraryEntry, AudiobookLibraryScan } from '../-audiobooks.types';

import { deleteLibraryBook, fetchLibrary, scanLibrary } from '../-audiobooks.api';
import styles from './audiobook-library.module.css';
import { AudiobookOverlay } from './audiobook-overlay';

function size(bytes: number): string {
  return bytes >= 1024 ** 3
    ? `${(bytes / 1024 ** 3).toFixed(1)} GB`
    : `${Math.round(bytes / 1024 ** 2)} MB`;
}

function duration(minutes: number): string {
  return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}m`;
}

function Cover({ book }: { book: AudiobookLibraryEntry }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className={styles.art}>
      {book.cover_url && !failed ? (
        <img src={book.cover_url} alt="" loading="lazy" onError={() => setFailed(true)} />
      ) : (
        <div className={styles.coverFallback} aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4">
            <path d="M4 14v-3a8 8 0 0 1 16 0v3M4 13H2v7h5v-7H4Zm16 0h2v7h-5v-7h3Z" />
          </svg>
          <span>{book.title}</span>
          <small>{book.author || 'AUDIOBOOK'}</small>
        </div>
      )}
      <span className={styles.format}>{book.audio_format.toUpperCase() || 'AUDIO'}</span>
    </div>
  );
}

export function AudiobookLibraryModal({ onClose }: { onClose: () => void }) {
  const [books, setBooks] = useState<AudiobookLibraryEntry[]>([]);
  const [root, setRoot] = useState('');
  const [scan, setScan] = useState<AudiobookLibraryScan>({ status: 'never' });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');
  const [filter, setFilter] = useState('');
  const [sort, setSort] = useState('title');
  const [localOnly, setLocalOnly] = useState(false);
  const [confirm, setConfirm] = useState('');
  const [revision, setRevision] = useState(0);
  const [queuedAt, setQueuedAt] = useState(0);
  const scanBaseline = useRef(0);

  useEffect(() => {
    let active = true;
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const data = await fetchLibrary();
        if (!active) return;
        setBooks(data.books);
        setRoot(data.root);
        setScan(data.scan);
        setLoadError('');
        if (queuedAt && (data.scan.started_at || 0) > scanBaseline.current) {
          setQueuedAt(0);
          setMessage('');
        } else if (queuedAt && Date.now() - queuedAt > 45_000) {
          setQueuedAt(0);
          setMessage('The scan has not started. Check its run history on the Automations page.');
        }
      } catch (error) {
        if (active)
          setLoadError(error instanceof Error ? error.message : 'Could not read your library.');
      } finally {
        pending = false;
        if (active) setLoading(false);
      }
    };
    void refresh();
    const timer = window.setInterval(
      () => void refresh(),
      scan.status === 'running' || queuedAt ? 3000 : 30000,
    );
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [revision, queuedAt, scan.status]);

  const localCount = books.filter((book) => book.asin.startsWith('local:')).length;
  const totalBytes = books.reduce((sum, book) => sum + book.size_bytes, 0);
  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return books
      .filter(
        (book) =>
          (!localOnly || book.asin.startsWith('local:')) &&
          (!needle ||
            [book.title, book.author, book.narrator, book.series_title].some((s) =>
              s.toLowerCase().includes(needle),
            )),
      )
      .sort((a, b) =>
        sort === 'recent'
          ? b.imported_at - a.imported_at
          : sort === 'size'
            ? b.size_bytes - a.size_bytes
            : (sort === 'author' ? a.author.localeCompare(b.author) : 0) ||
              a.title.localeCompare(b.title),
      );
  }, [books, filter, sort, localOnly]);

  const startScan = async () => {
    scanBaseline.current = scan.started_at || 0;
    setQueuedAt(Date.now());
    setMessage('');
    try {
      await scanLibrary();
    } catch (error) {
      setQueuedAt(0);
      setMessage(error instanceof Error ? error.message : 'Could not start the scan.');
    }
  };

  const remove = async (book: AudiobookLibraryEntry) => {
    setBusy(book.asin);
    try {
      const result = await deleteLibraryBook(book.asin);
      if (result.ok) {
        setBooks((prev) => prev.filter((b) => b.asin !== book.asin));
        setConfirm('');
        setRevision((value) => value + 1);
        setMessage(
          result.recycled
            ? `“${book.title}” moved to the recycle bin.`
            : `“${book.title}” was deleted.`,
        );
      } else setMessage(result.error || 'Could not delete that book.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not delete that book.');
    } finally {
      setBusy('');
    }
  };

  const scanning = scan.status === 'running' || queuedAt > 0;
  const scanText = queuedAt
    ? 'Starting scan…'
    : scan.status === 'running'
      ? `Scanning folder · ${scan.checked || 0} books checked`
      : scan.status === 'completed' && scan.finished_at
        ? `Last scanned ${new Date(scan.finished_at * 1000).toLocaleString()}`
        : scan.status === 'never'
          ? 'This folder has not been scanned yet'
          : 'Scan needs attention';

  return (
    <AudiobookOverlay onClose={onClose} label="Your audiobook library">
      <section className={styles.modal}>
        <header className={styles.header}>
          <div>
            <span className={styles.eyebrow}>Audiobooks / On your shelves</span>
            <h2>Your library</h2>
            <p>
              {loading
                ? 'Reading your library…'
                : `${books.length} ${books.length === 1 ? 'book' : 'books'} · ${size(totalBytes)} on disk`}
            </p>
          </div>
          <button
            type="button"
            className={styles.close}
            onClick={onClose}
            aria-label="Close library"
          >
            ×
          </button>
        </header>

        <div className={styles.scanPanel}>
          <div className={styles.scanInfo}>
            <strong>
              <span className={scanning ? styles.activeDot : styles.dot} />
              {scanText}
            </strong>
            <span className={styles.root} title={root}>
              {root || 'Set your audiobook folder in Settings → Library'}
            </span>
            {scan.status === 'completed' && (
              <small>
                {scan.adopted || 0} added · {scan.updated || 0} refreshed · {scan.removed || 0}{' '}
                missing
              </small>
            )}
          </div>
          <div className={styles.scanActions}>
            <button
              type="button"
              className={styles.primary}
              onClick={() => void startScan()}
              disabled={scanning || loading}
            >
              {scanning ? 'Scanning…' : 'Scan folder'}
            </button>
            <Link to="/automations" search={{ action: 'audiobook_scan_library' }} onClick={onClose}>
              Schedule & history ↗
            </Link>
          </div>
        </div>
        {scan.error && (
          <p className={styles.notice} role="status">
            {scan.error}
          </p>
        )}
        {message && (
          <p className={styles.notice} role="status">
            {message}
          </p>
        )}
        {loadError && (
          <p className={styles.notice} role="alert">
            {loadError}{' '}
            <button type="button" onClick={() => setRevision((v) => v + 1)}>
              Retry
            </button>
          </p>
        )}

        <div className={styles.toolbar}>
          <input
            type="search"
            aria-label="Search library"
            placeholder="Search title, author, narrator or series…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <select aria-label="Sort library" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="title">Title A–Z</option>
            <option value="author">Author A–Z</option>
            <option value="recent">Recently added</option>
            <option value="size">Largest first</option>
          </select>
        </div>
        <div className={styles.filters}>
          <button type="button" aria-pressed={!localOnly} onClick={() => setLocalOnly(false)}>
            All books <span>{books.length}</span>
          </button>
          <button type="button" aria-pressed={localOnly} onClick={() => setLocalOnly(true)}>
            Local only <span>{localCount}</span>
          </button>
          <span className={styles.filterHint}>
            Local books are indexed without guessing an Audible edition.
          </span>
        </div>

        <div className={styles.body} aria-busy={loading}>
          {loading ? (
            <p className={styles.empty}>Loading your shelves…</p>
          ) : books.length === 0 && !loadError ? (
            <div className={styles.empty}>
              <h3>Your books belong here</h3>
              <p>
                Scan your audiobook folder to find the books you already own, including those added
                outside SoulSync. Your files stay where they are.
              </p>
              <button
                type="button"
                className={styles.primary}
                disabled={scanning}
                onClick={() => void startScan()}
              >
                {scanning ? 'Scanning…' : 'Scan my folder'}
              </button>
            </div>
          ) : shown.length === 0 && !loadError ? (
            <p className={styles.empty}>No books match these filters.</p>
          ) : (
            <ul className={styles.grid}>
              {shown.map((book) => (
                <li className={styles.card} key={book.asin}>
                  {book.asin.startsWith('local:') ? (
                    <Cover book={book} />
                  ) : (
                    <Link
                      to="/audiobooks/$asin"
                      params={{ asin: book.asin }}
                      onClick={onClose}
                      aria-label={`View ${book.title}`}
                    >
                      <Cover book={book} />
                    </Link>
                  )}
                  <h3 title={book.title}>{book.title}</h3>
                  <p className={styles.author} title={book.author}>
                    {book.author || 'Unknown author'}
                  </p>
                  {book.series_title && (
                    <p className={styles.series} title={book.series_title}>
                      {book.series_title}
                      {book.series_sequence ? ` · ${book.series_sequence}` : ''}
                    </p>
                  )}
                  <p className={styles.facts}>
                    {book.runtime_minutes > 0 ? `${duration(book.runtime_minutes)} · ` : ''}
                    {size(book.size_bytes)}
                  </p>
                  <details className={styles.details}>
                    <summary>
                      File details{book.asin.startsWith('local:') ? ' · Local' : ''}
                    </summary>
                    <p>
                      {book.file_count} {book.file_count === 1 ? 'audio file' : 'audio files'}
                    </p>
                    {book.narrator && <p>Narrated by {book.narrator}</p>}
                    <p className={styles.path}>{book.path}</p>
                    {confirm === book.asin ? (
                      <div className={styles.confirm}>
                        <p>Delete this book from disk? Your recycle bin settings apply.</p>
                        <button
                          type="button"
                          disabled={scanning || !!busy}
                          className={styles.danger}
                          onClick={() => void remove(book)}
                        >
                          {busy === book.asin ? 'Deleting…' : 'Confirm delete'}
                        </button>
                        <button
                          type="button"
                          disabled={scanning || !!busy}
                          onClick={() => setConfirm('')}
                        >
                          Keep book
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className={styles.danger}
                        disabled={scanning || !!busy}
                        onClick={() => setConfirm(book.asin)}
                      >
                        Delete from disk
                      </button>
                    )}
                  </details>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </AudiobookOverlay>
  );
}
