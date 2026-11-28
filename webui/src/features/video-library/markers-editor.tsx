/**
 * skip markers on a movie's or an episode's page, for an admin: where the
 * intro, recap and credits are, who found each one (you, an .edl beside the
 * file, soulsync, plex, jellyfin, the file's chapters), and a small editor
 * to set, nudge, preview and remove them. a removed marker stays removed
 * when soulsync looks again; "Back to automatic" undoes an edit.
 * api/video/markers.py.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  fetchMarkers,
  findMarkersNow,
  type MarkerFile,
  type MarkerItemKind,
  type MarkerKind,
  type MarkerRow,
  removeMarker,
  resetMarker,
  setMarker,
} from './markers-api';
import s from './video-library.module.css';

const KINDS: { kind: MarkerKind; label: string }[] = [
  { kind: 'recap', label: 'Recap' },
  { kind: 'intro', label: 'Intro' },
  { kind: 'credits', label: 'Credits' },
];

const SOURCE_LABELS: Record<string, string> = {
  user: 'Set by you',
  sidecar: '.edl file',
  detected: 'Found by SoulSync',
  plex: 'From Plex',
  jellyfin: 'From Jellyfin',
  chapter: 'Chapter name',
};

/** 61.25 -> "1:01.25", 3725.5 -> "1:02:05.50" */
export function stamp(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00.00';
  const whole = Math.floor(seconds);
  const frac = Math.round((seconds - whole) * 100);
  const carry = frac === 100 ? 1 : 0;
  const total = whole + carry;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = String(total % 60).padStart(2, '0');
  const cs = String(carry ? 0 : frac).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}.${cs}` : `${m}:${sec}.${cs}`;
}

/** "1:01.25", "61.25", "1:02:05" -> seconds; null when it isn't a time */
export function parseStamp(text: string): number | null {
  const t = text.trim();
  if (!/^\d+(:\d{1,2}){0,2}(\.\d+)?$/.test(t)) return null;
  const parts = t.split(':');
  let total = 0;
  for (const p of parts) total = total * 60 + Number(p);
  return Number.isFinite(total) ? Math.round(total * 1000) / 1000 : null;
}

function TimeField({
  label,
  value,
  onChange,
  onPreview,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  onPreview: () => void;
}) {
  const [text, setText] = useState(stamp(value));
  useEffect(() => setText(stamp(value)), [value]);
  const nudge = (d: number) => onChange(Math.max(0, Math.round((value + d) * 1000) / 1000));
  return (
    <div className={s.mkField}>
      <span className={s.mkFieldLabel}>{label}</span>
      <div className={s.mkFieldRow}>
        <button
          type="button"
          className={s.mkNudge}
          onClick={() => nudge(-1)}
          aria-label={`${label} one second earlier`}
        >
          −1s
        </button>
        <button
          type="button"
          className={s.mkNudge}
          onClick={() => nudge(-0.1)}
          aria-label={`${label} a tenth earlier`}
        >
          −0.1
        </button>
        <input
          className={s.mkTime}
          value={text}
          aria-label={label}
          inputMode="decimal"
          onChange={(e) => setText(e.target.value)}
          onBlur={() => {
            const v = parseStamp(text);
            if (v === null) setText(stamp(value));
            else onChange(v);
          }}
        />
        <button
          type="button"
          className={s.mkNudge}
          onClick={() => nudge(0.1)}
          aria-label={`${label} a tenth later`}
        >
          +0.1
        </button>
        <button
          type="button"
          className={s.mkNudge}
          onClick={() => nudge(1)}
          aria-label={`${label} one second later`}
        >
          +1s
        </button>
        <button type="button" className={s.quietBtn} onClick={onPreview}>
          Preview
        </button>
      </div>
    </div>
  );
}

/** a few seconds of the file around a time, muted until asked */
function Preview({
  kind,
  itemId,
  fileId,
  at,
}: {
  kind: MarkerItemKind;
  itemId: number;
  fileId: number;
  at: number | null;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const el = video.current;
    if (!el || at === null) return;
    setFailed(false);
    const from = Math.max(0, at - 3);
    const go = () => {
      el.currentTime = from;
      void el.play().catch(() => undefined);
    };
    if (el.readyState >= 1) go();
    else el.addEventListener('loadedmetadata', go, { once: true });
    const stop = () => {
      if (el.currentTime >= at + 3) el.pause();
    };
    el.addEventListener('timeupdate', stop);
    return () => el.removeEventListener('timeupdate', stop);
  }, [at]);
  if (at === null) return null;
  return (
    <div className={s.mkPreview}>
      {failed ? (
        <p className={s.empty}>
          This file doesn&apos;t play in the browser as it is. Check the time in the player instead.
        </p>
      ) : (
        <video
          ref={video}
          className={s.mkVideo}
          src={`/api/player/stream/${kind}/${itemId}?part=${fileId}`}
          preload="metadata"
          controls
          playsInline
          onError={() => setFailed(true)}
        />
      )}
      <span className={s.mkPreviewNote}>Plays from 3 seconds before {stamp(at)} to 3 after.</span>
    </div>
  );
}

function MarkerLine({
  kindLabel,
  marker,
  removed,
  edited,
  onEdit,
  onRemove,
  onReset,
}: {
  kindLabel: string;
  marker: MarkerRow | undefined;
  removed: boolean;
  edited: boolean;
  onEdit: () => void;
  onRemove: () => void;
  onReset: () => void;
}) {
  return (
    <div className={s.mkLine}>
      <span className={s.mkKind}>{kindLabel}</span>
      {marker ? (
        <>
          <span className={s.mkSpan}>
            {stamp(marker.start)} – {stamp(marker.end)}
          </span>
          <span
            className={s.chip}
            title={
              marker.confidence ? `confidence ${Math.round(marker.confidence * 100)}%` : undefined
            }
          >
            {SOURCE_LABELS[marker.source] ?? marker.source}
          </span>
          {marker.post_credits ? (
            <span className={s.mkNote}>Scene after, from {stamp(marker.post_credits.start)}</span>
          ) : null}
        </>
      ) : (
        <span className={s.mkNone}>{removed ? 'Removed' : 'None found'}</span>
      )}
      <span className={s.mkActions}>
        <button type="button" className={s.quietBtn} onClick={onEdit}>
          {marker ? 'Adjust' : 'Set'}
        </button>
        {marker ? (
          <button type="button" className={s.quietBtn} onClick={onRemove}>
            Remove
          </button>
        ) : null}
        {edited || removed ? (
          <button type="button" className={s.quietBtn} onClick={onReset}>
            Back to automatic
          </button>
        ) : null}
      </span>
    </div>
  );
}

function Editor({
  kind,
  itemId,
  file,
  marker,
  markerKind,
  onSave,
  onCancel,
  busy,
}: {
  kind: MarkerItemKind;
  itemId: number;
  file: MarkerFile;
  marker: MarkerRow | undefined;
  markerKind: MarkerKind;
  onSave: (start: number, end: number, season: boolean) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const dur = file.duration ?? 0;
  const fallback =
    markerKind === 'credits'
      ? [Math.max(0, dur - 90), dur || 90]
      : markerKind === 'recap'
        ? [0, 45]
        : [60, 150];
  const [start, setStart] = useState(marker?.start ?? fallback[0]);
  const [end, setEnd] = useState(marker?.end ?? fallback[1]);
  const [season, setSeason] = useState(false);
  const [preview, setPreview] = useState<number | null>(null);
  const bad = end - start < 1 || (dur > 0 && start >= dur);
  return (
    <div className={s.mkEditor}>
      <TimeField
        label="Starts"
        value={start}
        onChange={setStart}
        onPreview={() => setPreview(start)}
      />
      <TimeField label="Ends" value={end} onChange={setEnd} onPreview={() => setPreview(end)} />
      <Preview kind={kind} itemId={itemId} fileId={file.file_id} at={preview} />
      {kind === 'episode' ? (
        <label className={s.mkCheck}>
          <input type="checkbox" checked={season} onChange={(e) => setSeason(e.target.checked)} />
          Use these times for every episode of the season
        </label>
      ) : null}
      {bad ? (
        <p className={s.mkWarn}>The end has to come at least a second after the start.</p>
      ) : null}
      <div className={s.mkEditorActions}>
        <button type="button" className={s.quietBtn} onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className={s.btnPrimary}
          disabled={bad || busy}
          onClick={() => onSave(start, end, season)}
        >
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  );
}

function analysisWords(f: MarkerFile): string {
  const a = f.analysis;
  if (a.status === 'done') return 'Looked at';
  if (a.status === 'running') return 'Looking now…';
  if (a.status === 'failed') return `Couldn’t look: ${a.error ?? 'unknown error'}`;
  return 'Waiting its turn';
}

export function MarkersPanel({
  kind,
  itemId,
  canManage,
}: {
  kind: MarkerItemKind;
  itemId: number;
  canManage: boolean;
}) {
  const [files, setFiles] = useState<MarkerFile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pick, setPick] = useState(0);
  const [editing, setEditing] = useState<MarkerKind | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(() => {
    let live = true;
    fetchMarkers(kind, itemId)
      .then((d) => live && (setFiles(d.files), setError(null)))
      .catch((e: Error) => live && setError(e.message || 'Couldn’t read the markers'));
    return () => {
      live = false;
    };
  }, [kind, itemId]);
  useEffect(() => (canManage ? load() : undefined), [load, canManage]);

  if (!canManage) return null;
  if (error) return <p className={s.empty}>{error}</p>;
  if (!files) return <p className={s.loading}>Reading the skip markers…</p>;
  if (!files.length) return null;
  const file = files[Math.min(pick, files.length - 1)];

  const act = async (work: () => Promise<{ files: MarkerFile[] }>) => {
    setBusy(true);
    try {
      const d = await work();
      setFiles(d.files);
      setEditing(null);
      setError(null);
    } catch (e) {
      setNote((e as Error).message || 'That didn’t work');
    } finally {
      setBusy(false);
    }
  };

  const find = async () => {
    setBusy(true);
    try {
      await findMarkersNow(kind === 'movie' ? { movie_id: itemId } : { episode_id: itemId });
      setNote('Queued first. The markers show here when it’s done.');
    } catch (e) {
      setNote((e as Error).message || 'Couldn’t queue it');
    } finally {
      setBusy(false);
    }
  };

  const shown = (k: MarkerKind) => file.markers.find((m) => m.kind === k);
  const edited = (k: MarkerKind) => file.sources.some((m) => m.kind === k && m.source === 'user');
  const kinds = kind === 'movie' ? KINDS.filter((k) => k.kind === 'credits') : KINDS;

  return (
    <section className={s.mkPanel} aria-label="Skip markers">
      <div className={s.sectionHead}>
        <h3 className={s.sectionTitle}>Skip markers</h3>
        <span className={s.headActions}>
          <span className={s.mkStatus}>{analysisWords(file)}</span>
          <button type="button" className={s.quietBtn} disabled={busy} onClick={() => void find()}>
            {kind === 'movie' ? 'Find credits now' : 'Find intros now'}
          </button>
        </span>
      </div>
      {files.length > 1 ? (
        <label className={s.mkFile}>
          File
          <select value={pick} onChange={(e) => setPick(Number(e.target.value))}>
            {files.map((f, i) => (
              <option key={f.file_id} value={i}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {kinds.map(({ kind: k, label }) =>
        editing === k ? (
          <div key={k} className={s.mkLine} data-open="true">
            <span className={s.mkKind}>{label}</span>
            <Editor
              kind={kind}
              itemId={itemId}
              file={file}
              marker={shown(k)}
              markerKind={k}
              busy={busy}
              onCancel={() => setEditing(null)}
              onSave={(start, end, season) =>
                void act(() =>
                  setMarker(kind, itemId, {
                    kind: k,
                    start,
                    end,
                    file_id: file.file_id,
                    scope: season ? 'season' : 'file',
                  }),
                )
              }
            />
          </div>
        ) : (
          <MarkerLine
            key={k}
            kindLabel={label}
            marker={shown(k)}
            removed={file.removed.includes(k)}
            edited={edited(k)}
            onEdit={() => setEditing(k)}
            onRemove={() => void act(() => removeMarker(kind, itemId, k, file.file_id))}
            onReset={() => void act(() => resetMarker(kind, itemId, k, file.file_id))}
          />
        ),
      )}
      <p className={s.mkFoot}>
        {file.sidecar.writes
          ? file.sidecar.reachable
            ? 'Saved beside the video as an .edl too, so Kodi skips them as well.'
            : 'Kept in SoulSync: the file is on a drive this server can’t write to.'
          : 'Kept in SoulSync. The library can also save them beside each video (Settings, Video, its library).'}
      </p>
      {note ? (
        <p className={s.mkStatus} role="status">
          {note}
        </p>
      ) : null}
    </section>
  );
}
