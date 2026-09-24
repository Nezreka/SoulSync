import { Dialog } from '@base-ui/react/dialog';
import { useEffect, useMemo, useState } from 'react';

import { DialogFrame } from '@/components/dialog';

import type { EnrichedFile, ManualAlbum } from '../-basic.enriched';
import type { DownloadTarget } from '../-basic.types';

import { fileKey, startManual, titleFromFilename, toEnrichedFile } from '../-basic.enriched';
import { qualityLabel } from '../-basic.helpers';
import styles from './manual-modal.module.css';

const TYPES = [
  { value: 'album', label: 'Album' },
  { value: 'live', label: 'Live' },
  { value: 'ep', label: 'EP' },
  { value: 'single', label: 'Single' },
] as const;

/** covers bigger than this are refused in the browser, not uploaded and failed */
const MAX_COVER_BYTES = 12 * 1024 * 1024;

interface Row {
  key: string;
  file: EnrichedFile;
  title: string;
  number: number;
  disc: number;
}

function prefill(target: DownloadTarget) {
  if (target.kind === 'album') {
    const { album } = target;
    const artist = album.artist || '';
    const files = (album.tracks ?? []).map((t) => toEnrichedFile(t, album.album_title));
    return {
      name: album.album_title || '',
      artist,
      date: album.year || '',
      type: 'album',
      detail: `${files.length} files from ${album.username}`,
      quality: qualityLabel(album),
      rows: files.map((file, i) => ({
        key: fileKey(file),
        file,
        title: file.title || titleFromFilename(file.filename, artist),
        number: file.track_number || i + 1,
        disc: 1,
      })),
    };
  }
  const track = target.kind === 'track' ? target.track : target.album.tracks[target.trackIndex];
  const file = toEnrichedFile(track, target.kind === 'albumTrack' ? target.album.album_title : '');
  const artist = track.artist || (target.kind === 'albumTrack' ? target.album.artist : '') || '';
  const title = track.title || titleFromFilename(track.filename, artist);
  return {
    // one file on its own is a single until the user says otherwise
    name: target.kind === 'albumTrack' ? target.album.album_title || title : title,
    artist,
    date: target.kind === 'albumTrack' ? target.album.year || '' : '',
    type: target.kind === 'albumTrack' ? 'album' : 'single',
    detail: `1 file from ${track.username}`,
    quality: qualityLabel(track),
    rows: [{ key: fileKey(file), file, title, number: track.track_number || 1, disc: 1 }],
  };
}

function year(date: string) {
  return (date.match(/\d{4}/) ?? [''])[0];
}

/**
 * Tag it yourself: for recordings no metadata service knows (bootlegs, live
 * sets, mixtapes). the user types the release, SoulSync tags and files it
 * like any other, and then leaves it alone.
 */
export function ManualModal({
  target,
  onClose,
}: {
  target: DownloadTarget | null;
  onClose: () => void;
}) {
  const seed = useMemo(() => (target ? prefill(target) : null), [target]);
  const [name, setName] = useState('');
  const [artist, setArtist] = useState('');
  const [date, setDate] = useState('');
  const [genre, setGenre] = useState('');
  const [type, setType] = useState('album');
  const [rows, setRows] = useState<Row[]>([]);
  const [coverUrl, setCoverUrl] = useState('');
  const [coverData, setCoverData] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!seed) return;
    setName(seed.name);
    setArtist(seed.artist);
    setDate(seed.date);
    setGenre('');
    setType(seed.type);
    setRows(seed.rows);
    setCoverUrl('');
    setCoverData('');
    setError('');
    setBusy(false);
  }, [seed]);

  if (!target || !seed) return null;

  const multiDisc = rows.some((r) => r.disc > 1);
  const missing = !name.trim()
    ? 'Give the album a name.'
    : !artist.trim()
      ? 'Who made it?'
      : rows.some((r) => !r.title.trim())
        ? 'Every track needs a title.'
        : '';
  const cover = coverData || coverUrl;

  function readCover(file: File | undefined) {
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      setError('That file isn’t an image.');
      return;
    }
    if (file.size > MAX_COVER_BYTES) {
      setError('That image is over 12 MB. Try a smaller one.');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setCoverData(String(reader.result || ''));
      setCoverUrl('');
      setError('');
    };
    reader.readAsDataURL(file);
  }

  function update(index: number, patch: Partial<Row>) {
    setRows((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  function fillFromFilenames() {
    setRows((prev) =>
      prev.map((r) => ({ ...r, title: titleFromFilename(r.file.filename, artist) || r.title })),
    );
  }

  async function submit(ignoreBlocklist = false): Promise<void> {
    if (missing || !seed) return;
    setBusy(true);
    setError('');
    const album: ManualAlbum = {
      name: name.trim(),
      artist: artist.trim(),
      date: date.trim(),
      genre: genre.trim(),
      type,
      ...(coverData
        ? { image_data: coverData }
        : coverUrl.trim()
          ? { image_url: coverUrl.trim() }
          : {}),
    };
    try {
      const result = await startManual({
        files: rows.map((r) => r.file),
        album,
        tracks: rows.map((r) => ({
          file_key: r.key,
          title: r.title.trim(),
          track_number: r.number,
          disc_number: r.disc,
        })),
        ...(ignoreBlocklist ? { ignore_blocklist: true } : {}),
      });
      if (result.blocked && !ignoreBlocklist) {
        const who = result.blocked_name || 'This artist';
        const ok = await window.showConfirmDialog?.({
          title: 'On your blocklist',
          message: `${who} is on your blocklist. Download this anyway?`,
          confirmText: 'Download anyway',
          cancelText: 'Skip',
        });
        if (ok) return submit(true);
        setBusy(false);
        return;
      }
      if (!result.success) {
        setError(result.error || 'Could not start the download.');
        setBusy(false);
        return;
      }
      window.showToast?.(result.message || 'Download started', 'success');
      onClose();
    } catch {
      setError('Could not start the download.');
      setBusy(false);
    }
  }

  const filedAs =
    [artist.trim(), name.trim()].filter(Boolean).join(' / ') +
    (year(date) ? ` (${year(date)})` : '');

  return (
    <DialogFrame open onOpenChange={(open) => !open && onClose()} className={styles.popup}>
      <div className={styles.head}>
        <div className={styles.meta}>
          <Dialog.Title className={styles.title}>Tag it yourself</Dialog.Title>
          <div className={styles.sub}>
            {seed.detail}
            {seed.quality ? <span className={styles.sep}>·</span> : null}
            {seed.quality}
          </div>
        </div>
        <Dialog.Close className={styles.close} aria-label="Close">
          ×
        </Dialog.Close>
      </div>

      <div className={styles.body}>
        <div className={styles.side}>
          <label
            className={styles.drop}
            data-has-cover={cover ? true : undefined}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              readCover(event.dataTransfer.files?.[0]);
            }}
          >
            {cover ? (
              <img src={cover} alt="Cover art" />
            ) : (
              <span className={styles.dropHint}>
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <rect
                    x="3.5"
                    y="3.5"
                    width="17"
                    height="17"
                    rx="3"
                    stroke="currentColor"
                    strokeWidth="1.5"
                  />
                  <circle cx="9" cy="9" r="1.8" stroke="currentColor" strokeWidth="1.5" />
                  <path
                    d="m20.5 15-4.5-4.5L6 20.5"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                  />
                </svg>
                Drop cover art or click to choose
              </span>
            )}
            <input
              type="file"
              accept="image/*"
              className={styles.fileInput}
              aria-label="Cover art"
              onChange={(event) => readCover(event.target.files?.[0])}
            />
          </label>
          <input
            className={styles.input}
            placeholder="or paste an image link"
            aria-label="Cover art link"
            value={coverData ? '' : coverUrl}
            disabled={Boolean(coverData)}
            onChange={(event) => setCoverUrl(event.target.value)}
          />
          {cover ? (
            <button
              type="button"
              className={styles.link}
              onClick={() => {
                setCoverData('');
                setCoverUrl('');
              }}
            >
              Remove cover
            </button>
          ) : null}

          <div className={styles.label}>Type</div>
          <div className={styles.kinds} role="radiogroup" aria-label="Release type">
            {TYPES.map((t) => (
              <button
                key={t.value}
                type="button"
                role="radio"
                aria-checked={type === t.value}
                onClick={() => setType(t.value)}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        <div className={styles.main}>
          <div className={styles.fields}>
            <label className={`${styles.field} ${styles.full}`}>
              <span className={styles.label}>Album</span>
              <input
                className={styles.input}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className={styles.field}>
              <span className={styles.label}>Album artist</span>
              <input
                className={styles.input}
                value={artist}
                onChange={(e) => setArtist(e.target.value)}
              />
            </label>
            <label className={styles.field}>
              <span className={styles.label}>Date</span>
              <input
                className={styles.input}
                value={date}
                placeholder="2003 or 2003-06-28"
                onChange={(e) => setDate(e.target.value)}
              />
            </label>
            <label className={`${styles.field} ${styles.full}`}>
              <span className={styles.label}>Genre</span>
              <input
                className={styles.input}
                value={genre}
                placeholder="optional"
                onChange={(e) => setGenre(e.target.value)}
              />
            </label>
          </div>

          <div className={styles.tracksHead}>
            <span className={styles.label}>Tracks</span>
            <button type="button" className={styles.link} onClick={fillFromFilenames}>
              Fill titles from filenames
            </button>
          </div>
          <div className={styles.tracks}>
            <div className={styles.trackHead} aria-hidden="true">
              <span>#</span>
              <span>Title</span>
              <span>Disc</span>
            </div>
            {rows.map((row, i) => {
              const fileName = row.file.filename.replace(/\\/g, '/').split('/').pop();
              return (
                <div key={row.key} className={styles.track}>
                  <input
                    className={styles.num}
                    type="number"
                    min={1}
                    value={row.number}
                    aria-label={`Track number for ${fileName}`}
                    onChange={(e) =>
                      update(i, { number: Math.max(1, Number(e.target.value) || 1) })
                    }
                  />
                  <div className={styles.trackMain}>
                    <input
                      className={styles.trackTitle}
                      value={row.title}
                      aria-label={`Title for ${fileName}`}
                      onChange={(e) => update(i, { title: e.target.value })}
                    />
                    <div className={styles.fileName}>{fileName}</div>
                  </div>
                  <input
                    className={styles.num}
                    type="number"
                    min={1}
                    value={row.disc}
                    title="Disc"
                    aria-label={`Disc for ${fileName}`}
                    data-dim={multiDisc ? undefined : true}
                    onChange={(e) => update(i, { disc: Math.max(1, Number(e.target.value) || 1) })}
                  />
                </div>
              );
            })}
          </div>

          <div className={styles.lock}>
            <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <rect
                x="4"
                y="9"
                width="12"
                height="8.5"
                rx="2"
                stroke="currentColor"
                strokeWidth="1.6"
              />
              <path
                d="M7 9V6.5a3 3 0 0 1 6 0V9"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </svg>
            <div>
              SoulSync keeps these details as you entered them.
              <span> It won’t rematch, retag or replace this album.</span>
            </div>
          </div>
          {error ? <p className={styles.error}>{error}</p> : null}
        </div>
      </div>

      <div className={styles.foot}>
        <span className={styles.note}>{missing || (filedAs ? `Filed as ${filedAs}` : '')}</span>
        <button type="button" className={styles.button} onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className={`${styles.button} ${styles.primary}`}
          disabled={Boolean(missing) || busy}
          onClick={() => void submit()}
        >
          {busy ? 'Starting…' : 'Download and tag'}
        </button>
      </div>
    </DialogFrame>
  );
}
