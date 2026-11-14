/**
 * one title, opened beside the list: its details to edit, its files, and
 * what happened to it. the things the music library's edit dialogs do for an
 * album, for a movie or a show.
 */

import { useEffect, useMemo, useState } from 'react';

import { DialogFrame } from '@/components/dialog';

import type { Facets, FileChange, FileRow, TitleKind, Undoable } from './types';

import {
  type HistoryRow,
  type TitleDetail,
  fetchChanges,
  fetchFiles,
  fetchTitleDetail,
  fetchTitleHistory,
  saveTitleFields,
  setFieldLock,
  startBulk,
} from './api';
import { channelsLabel, codecName, formatDate, formatSize, languageCodes, plural } from './format';
import { ChangeList, EpisodesTab, PadlockIcon, PosterPicker } from './manager-extras';
import { openTitle } from './manager-page';
import { posterUrl, ResBadge } from './manager-views';
import { EMPTY_FILTERS } from './types';
import s from './video-manager.module.css';

type Tab = 'details' | 'files' | 'episodes' | 'history';

const TAB_NAMES: Record<Tab, string> = {
  details: 'Details',
  files: 'Files',
  episodes: 'Episodes',
  history: 'History',
};

interface Draft {
  title: string;
  sort_title: string;
  year: string;
  content_rating: string;
  tagline: string;
  overview: string;
  genres: string;
  /** studio for a movie, network for a show */
  maker: string;
}

function draftOf(detail: TitleDetail, kind: TitleKind): Draft {
  return {
    title: detail.title ?? '',
    sort_title: detail.sort_title ?? '',
    year: detail.year != null ? String(detail.year) : '',
    content_rating: detail.content_rating ?? '',
    tagline: detail.tagline ?? '',
    overview: detail.overview ?? '',
    genres: (detail.genres ?? []).join(', '),
    maker: (kind === 'movie' ? detail.studio : detail.network) ?? '',
  };
}

/** only what was changed goes to the server: every saved field is locked */
export function changedFields(
  before: Draft,
  after: Draft,
  kind: TitleKind,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of ['title', 'sort_title', 'content_rating', 'tagline', 'overview'] as const)
    if (before[key].trim() !== after[key].trim()) out[key] = after[key].trim();
  if (before.year.trim() !== after.year.trim()) out.year = after.year.trim() || null;
  if (before.maker.trim() !== after.maker.trim())
    out[kind === 'movie' ? 'studio' : 'network'] = after.maker.trim();
  const list = (v: string) =>
    v
      .split(',')
      .map((g) => g.trim())
      .filter(Boolean);
  if (list(before.genres).join('|') !== list(after.genres).join('|'))
    out.genres = list(after.genres);
  return out;
}

function toast(message: string, type: 'success' | 'error' | 'info' | 'warning' = 'success') {
  window.showToast?.(message, type);
}

function LockMark({ locked, onRelease }: { locked: boolean; onRelease: () => void }) {
  if (!locked) return null;
  return (
    <button
      type="button"
      className={s.lock}
      title="You set this, so scans and metadata refreshes leave it alone. Click to hand it back."
      onClick={onRelease}
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.9}
        aria-hidden="true"
      >
        <path
          d="M7 11V8a5 5 0 0 1 10 0v3M6 11h12v9H6z"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      Yours
    </button>
  );
}

const OUTCOME: Record<string, string> = {
  completed: 'Downloaded',
  import_failed: 'Could not be imported',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

export function TitleSheet({
  kind,
  id,
  facets,
  onClose,
  onChanged,
}: {
  kind: TitleKind;
  id: number;
  facets: Facets | null;
  onClose: () => void;
  /** a change that moved files hands back what Undo would reverse */
  onChanged: (undoable?: Undoable) => void;
}) {
  const [tab, setTab] = useState<Tab>('details');
  const [detail, setDetail] = useState<TitleDetail | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saved, setSaved] = useState<Draft | null>(null);
  const [locks, setLocks] = useState<string[]>([]);
  const [files, setFiles] = useState<FileRow[] | null>(null);
  const [history, setHistory] = useState<HistoryRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [profile, setProfile] = useState<string>('');
  const [seriesType, setSeriesType] = useState('standard');
  const [locked, setLocked] = useState(false);
  const [changesMade, setChangesMade] = useState<FileChange[] | null>(null);
  const [picking, setPicking] = useState(false);
  /** bumped when the poster changes, so the image is asked for again */
  const [art, setArt] = useState(0);

  useEffect(() => {
    let live = true;
    fetchTitleDetail(kind, id)
      .then((d) => {
        if (!live) return;
        setDetail(d);
        const start = draftOf(d, kind);
        setDraft(start);
        setSaved(start);
        setLocks(d.locked_fields ?? []);
        setProfile(d.quality_profile_id ? String(d.quality_profile_id) : '');
        setSeriesType(d.series_type || 'standard');
        setLocked(Boolean(d.hands_off));
      })
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [kind, id]);

  useEffect(() => {
    if (tab !== 'files' || files) return;
    let live = true;
    fetchFiles(
      kind === 'show' ? 'shows' : 'movies',
      EMPTY_FILTERS,
      { by: 'title', dir: 'asc' },
      1,
      500,
      id,
    )
      .then((out) => live && setFiles(out.items))
      .catch(() => live && setFiles([]));
    return () => {
      live = false;
    };
  }, [tab, files, kind, id]);

  useEffect(() => {
    if (tab !== 'history' || history) return;
    let live = true;
    fetchTitleHistory(kind, id)
      .then((rows) => live && setHistory(rows))
      .catch(() => live && setHistory([]));
    return () => {
      live = false;
    };
  }, [tab, history, kind, id]);

  useEffect(() => {
    if (tab !== 'history' || changesMade) return;
    let live = true;
    fetchChanges({ kind, id })
      .then((out) => live && setChangesMade(out.items))
      .catch(() => live && setChangesMade([]));
    return () => {
      live = false;
    };
  }, [tab, changesMade, kind, id]);

  const changes = useMemo(
    () => (draft && saved ? changedFields(saved, draft, kind) : {}),
    [draft, saved, kind],
  );
  const dirty = Object.keys(changes).length > 0;

  const save = async () => {
    if (!draft || !dirty) return;
    setSaving(true);
    try {
      const res = await saveTitleFields(kind, id, changes);
      setSaved(draft);
      setLocks(res.locked ?? locks);
      toast(res.pushed ? 'Saved, and sent to your media server' : 'Saved');
      onChanged();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not save', 'error');
    } finally {
      setSaving(false);
    }
  };

  const release = async (field: string) => {
    try {
      await setFieldLock(kind, id, field, false);
      setLocks((prev) => prev.filter((f) => f !== field));
      toast('Handed back: the next scan sets it again');
      onChanged();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not release it', 'error');
    }
  };

  const changeProfile = async (value: string) => {
    setProfile(value);
    try {
      await startBulk(kind, [id], 'quality_profile', { profile_id: value ? Number(value) : null });
      toast('Quality profile set');
      onChanged();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not set the profile', 'error');
    }
  };

  const changeSeriesType = async (value: string) => {
    setSeriesType(value);
    try {
      await startBulk(kind, [id], 'series_type', { value });
      toast('Series type set');
      onChanged();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not set the series type', 'error');
    }
  };

  const togglePadlock = async () => {
    const next = !locked;
    setLocked(next);
    try {
      await startBulk(kind, [id], 'lock', { value: next });
      // locking takes every detail too; unlocking hands them all back
      window.setTimeout(() => {
        fetchTitleDetail(kind, id)
          .then((d) => setLocks(d.locked_fields ?? []))
          .catch(() => undefined);
        onChanged();
      }, 700);
      toast(
        next
          ? 'Locked: automation leaves it and its files alone'
          : 'Unlocked: scans and maintenance look after it again',
      );
    } catch (e) {
      setLocked(!next);
      toast(e instanceof Error ? e.message : 'Could not change the padlock', 'error');
    }
  };

  const field = (
    key: keyof Draft,
    label: string,
    lockName: string,
    opts: { area?: boolean; hint?: string; numeric?: boolean } = {},
  ) =>
    draft ? (
      <label className={s.field}>
        <span className={s.fieldHead}>
          <span className={s.fieldLabel}>{label}</span>
          <LockMark locked={locks.includes(lockName)} onRelease={() => void release(lockName)} />
        </span>
        {opts.area ? (
          <textarea
            className={s.textarea}
            rows={5}
            value={draft[key]}
            onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
          />
        ) : (
          <input
            className={s.input}
            inputMode={opts.numeric ? 'numeric' : undefined}
            value={draft[key]}
            onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
          />
        )}
        {opts.hint ? <span className={s.fieldHint}>{opts.hint}</span> : null}
      </label>
    ) : null;

  const totalSize = (files ?? []).reduce((sum, f) => sum + f.size_bytes, 0);

  return (
    <DialogFrame
      open
      onOpenChange={(open) => !open && onClose()}
      viewportClassName={s.sheetViewport}
      className={s.sheet}
      aria-label={detail?.title ?? 'Title'}
    >
      <header className={s.sheetHead}>
        <img
          className={s.sheetPoster}
          src={posterUrl(kind, id, 185) + (art ? `&v=${art}` : '')}
          alt=""
        />
        <div className={s.sheetHeadText}>
          <h2 className={s.sheetTitle}>{detail?.title ?? 'Loading…'}</h2>
          <p className={s.sheetMeta}>
            {[detail?.year, kind === 'movie' ? 'Movie' : 'Show'].filter(Boolean).join(' · ')}
          </p>
          <button
            type="button"
            className={s.linkBtn}
            onClick={() => (onClose(), openTitle(kind, id))}
          >
            Open its page
          </button>
          {detail?.tmdb_id ? (
            <button type="button" className={s.linkBtn} onClick={() => setPicking(true)}>
              Change poster
            </button>
          ) : null}
        </div>
        <div className={s.sheetActions}>
          {tab === 'details' && dirty ? (
            <button
              type="button"
              className="pgf-btn pgf-btn--main"
              disabled={saving}
              onClick={() => void save()}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          ) : null}
          {detail ? (
            <button
              type="button"
              className={s.padlock}
              aria-pressed={locked}
              title={
                locked
                  ? 'Locked. Maintenance jobs, mass rename and automatic upgrades leave this title and its files alone, and its details are kept. Click to unlock.'
                  : 'Lock this title once it is the way you want it. Automation then leaves it and its files alone.'
              }
              onClick={() => void togglePadlock()}
            >
              <PadlockIcon open={!locked} />
              {locked ? 'Locked' : 'Lock'}
            </button>
          ) : null}
          <button type="button" className={s.barClose} aria-label="Close" onClick={onClose}>
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              aria-hidden="true"
            >
              <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      </header>

      <div className={s.sheetTabs} role="tablist" aria-label="Sections">
        {(kind === 'show'
          ? (['details', 'files', 'episodes', 'history'] as const)
          : (['details', 'files', 'history'] as const)
        ).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            className={s.sheetTab}
            onClick={() => setTab(t)}
          >
            {TAB_NAMES[t]}
          </button>
        ))}
      </div>

      <div className={s.sheetBody}>
        {error ? <p className={s.dialogNote}>{error}</p> : null}

        {tab === 'details' && draft ? (
          <>
            {field('title', 'Title', 'title')}
            <div className={s.fieldRow}>
              {field('year', 'Year', 'year', { numeric: true })}
              {field('content_rating', 'Content rating', 'content_rating')}
            </div>
            {field('sort_title', 'Sort as', 'sort_title', {
              hint: 'Where it sits in A to Z. Left alone, it follows the title.',
            })}
            {field('genres', 'Genres', 'genres', { hint: 'Separate with commas.' })}
            {field(
              'maker',
              kind === 'movie' ? 'Studio' : 'Network',
              kind === 'movie' ? 'studio' : 'network',
            )}
            {field('tagline', 'Tagline', 'tagline')}
            {field('overview', 'Overview', 'overview', { area: true })}
            <label className={s.field}>
              <span className={s.fieldLabel}>Quality profile</span>
              <select
                className={s.select}
                value={profile}
                onChange={(e) => void changeProfile(e.target.value)}
              >
                <option value="">Default</option>
                {(facets?.quality_profiles ?? []).map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    {p.name}
                  </option>
                ))}
              </select>
              <span className={s.fieldHint}>
                What searches and upgrades for this title aim for.
              </span>
            </label>
            {kind === 'show' ? (
              <label className={s.field}>
                <span className={s.fieldLabel}>Series type</span>
                <select
                  className={s.select}
                  value={seriesType}
                  onChange={(e) => void changeSeriesType(e.target.value)}
                >
                  <option value="standard">Standard (S01E02)</option>
                  <option value="daily">Daily (by air date)</option>
                  <option value="anime">Anime (absolute numbers)</option>
                </select>
                <span className={s.fieldHint}>
                  How its releases are named, which decides how they are searched for.
                </span>
              </label>
            ) : null}
            <p className={s.sheetNote}>
              What you change here is kept: scans and metadata refreshes leave those fields alone,
              and the change is sent to your media server.
            </p>
          </>
        ) : null}

        {tab === 'files' ? (
          files === null ? (
            <p className={s.dialogNote}>Reading the files…</p>
          ) : files.length === 0 ? (
            <p className={s.dialogNote}>No files on disk for this title.</p>
          ) : (
            <>
              <p className={s.dialogNote}>
                <b>{plural(files.length, 'file', 'files')}</b> · {formatSize(totalSize)}
              </p>
              <ul className={s.fileList}>
                {files.map((f) => (
                  <li key={f.id} className={s.fileItem}>
                    <div className={s.fileItemHead}>
                      <ResBadge label={f.resolution} />
                      {f.season_number != null ? (
                        <span className={s.muted}>
                          S{String(f.season_number).padStart(2, '0')}E
                          {String(f.episode_number ?? 0).padStart(2, '0')}
                        </span>
                      ) : null}
                      <span className={s.fileItemSize}>{formatSize(f.size_bytes)}</span>
                    </div>
                    <span className={s.fileName} title={f.path}>
                      {f.filename}
                    </span>
                    <span className={s.muted}>
                      {[
                        codecName(f.video_codec),
                        f.dynamic_range,
                        codecName(f.audio_codec),
                        f.atmos ? 'Atmos' : channelsLabel(f.audio_channels),
                        f.audio_languages.length ? `Audio ${languageCodes(f.audio_languages)}` : '',
                        f.subtitle_languages.length
                          ? `Subs ${languageCodes(f.subtitle_languages, 4)}`
                          : '',
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                    <span className={s.filePathLine}>{f.path}</span>
                  </li>
                ))}
              </ul>
            </>
          )
        ) : null}

        {tab === 'episodes' && kind === 'show' ? (
          <EpisodesTab
            showId={id}
            onChanged={(done) => {
              setFiles(null);
              setChangesMade(null);
              onChanged(done);
            }}
          />
        ) : null}

        {tab === 'history' && changesMade && changesMade.length ? (
          <>
            <p className={s.sheetSection}>Changes to its files</p>
            <ChangeList
              changes={changesMade}
              showTitle={false}
              onUndone={() => {
                setChangesMade(null);
                setFiles(null);
                onChanged();
              }}
            />
            <p className={s.sheetSection}>Downloads</p>
          </>
        ) : null}

        {tab === 'history' ? (
          history === null ? (
            <p className={s.dialogNote}>Loading…</p>
          ) : history.length === 0 ? (
            <p className={s.dialogNote}>
              Nothing has been downloaded for this title through SoulSync yet.
            </p>
          ) : (
            <ol className={s.history}>
              {history.map((h) => (
                <li key={h.id} className={s.historyItem} data-outcome={h.outcome ?? 'completed'}>
                  <span className={s.historyDot} aria-hidden="true" />
                  <div>
                    <p className={s.historyWhat}>
                      {OUTCOME[h.outcome ?? 'completed'] ?? h.outcome}
                      {h.season_number != null
                        ? ` · S${String(h.season_number).padStart(2, '0')}E${String(h.episode_number ?? 0).padStart(2, '0')}`
                        : ''}
                      {h.quality_label ? ` · ${h.quality_label}` : ''}
                    </p>
                    <p className={s.historyRelease}>{h.release_title || h.title}</p>
                    <p className={s.muted}>
                      {[
                        formatDate(h.completed_at || h.grabbed_at || h.created_at),
                        h.source,
                        formatSize(h.size_bytes),
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </p>
                    {h.error ? <p className={s.historyError}>{h.error}</p> : null}
                  </div>
                </li>
              ))}
            </ol>
          )
        ) : null}
      </div>
      {picking && detail?.tmdb_id ? (
        <PosterPicker
          kind={kind}
          id={id}
          tmdbId={detail.tmdb_id}
          onPicked={() => {
            setArt(Date.now());
            onChanged();
          }}
          onClose={() => setPicking(false)}
        />
      ) : null}
    </DialogFrame>
  );
}
