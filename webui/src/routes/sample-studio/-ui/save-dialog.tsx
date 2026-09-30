import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import type {
  RenderFx,
  StashEntry,
  StashFormat,
  StemName,
  StudioTrack,
} from '../-sample-studio.types';

import {
  previewAudioUrl,
  requestPreview,
  saveChop,
  studioSampleFoldersQueryOptions,
} from '../-sample-studio.api';
import { describeRenderParams, formatTime, suggestChopName } from '../-sample-studio.helpers';
import { STEM_LABEL, STASH_FORMAT_LABEL } from '../-sample-studio.types';
import { SaveIcon, SpeakerIcon } from './icons';
import styles from './sample-studio-page.module.css';

interface SaveDialogProps {
  open: boolean;
  onClose: () => void;
  track: StudioTrack;
  start: number;
  end: number;
  pitchSt: number;
  targetBpm: number | null;
  /** Which stem the chop is cut from — null means the full mix. */
  stem: StemName | null;
  /** FX to bake into the save — same render params as the preview. */
  fx: RenderFx;
  onSaved: (entry: StashEntry) => void;
}

const FORMATS: StashFormat[] = ['wav16', 'wav24', 'flac'];

/**
 * Save-chop dialog: editable auto-suggested name, tags, format choice,
 * audition of the exact render, then POST /api/sample/chop (file + bookmark).
 */
export function SaveDialog({
  open,
  onClose,
  track,
  start,
  end,
  pitchSt,
  targetBpm,
  stem,
  fx,
  onSaved,
}: SaveDialogProps) {
  const [name, setName] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState('');
  const [format, setFormat] = useState<StashFormat>('wav16');
  const [folder, setFolder] = useState<string | null>(null);
  const [auditionUrl, setAuditionUrl] = useState<string | null>(null);
  const [auditioning, setAuditioning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Configured sample folders — the picker defaults to the first.
  const foldersQuery = useQuery({ ...studioSampleFoldersQueryOptions(), enabled: open });
  const folders = foldersQuery.data?.folders ?? [];
  const selectedFolder = folder ?? folders[0] ?? null;

  // Fresh defaults every time the dialog opens.
  useEffect(() => {
    if (open) {
      setName(suggestChopName(track.title, start));
      setTags([]);
      setTagInput('');
      setFormat('wav16');
      setFolder(null);
      setAuditionUrl(null);
      setAuditioning(false);
      setSaving(false);
      setError(null);
    }
  }, [open, track.id, track.title, start]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (open) window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const addTag = () => {
    const t = tagInput.trim().toLowerCase().replace(/\s+/g, '-');
    if (t && !tags.includes(t) && tags.length < 20) setTags([...tags, t]);
    setTagInput('');
  };

  const audition = async () => {
    setError(null);
    setAuditioning(true);
    try {
      const preview = await requestPreview(track.id, {
        start,
        end,
        pitchSt,
        targetBpm,
        stem,
        fx,
      });
      const url = previewAudioUrl(preview.preview_id);
      setAuditionUrl(url);
      // Play on the next tick so the <audio> picks up the new src.
      window.setTimeout(() => void audioRef.current?.play().catch(() => {}), 0);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Preview failed');
    } finally {
      setAuditioning(false);
    }
  };

  const save = async () => {
    if (!name.trim() || saving) return;
    setError(null);
    setSaving(true);
    try {
      const entry = await saveChop(track.id, {
        start,
        end,
        pitchSt,
        targetBpm,
        stem,
        fx,
        name: name.trim(),
        tags,
        format,
        folder: selectedFolder,
      });
      onSaved(entry);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const fxBits: string[] = [];
  if (stem) fxBits.push(`${STEM_LABEL[stem]} stem`);
  if (Math.abs(pitchSt) >= 0.01) fxBits.push(`${pitchSt > 0 ? '+' : ''}${pitchSt} st`);
  if (targetBpm != null) fxBits.push(`→ ${targetBpm} BPM`);
  const renderBits = describeRenderParams(fx);
  if (renderBits.length > 0) fxBits.push(...renderBits);

  return (
    <div className={styles.dialogOverlay} onClick={onClose}>
      <div
        className={styles.dialog}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Save chop"
      >
        <h3 className={styles.dialogTitle}>Save chop</h3>

        <label className={styles.dialogField}>
          <span>Name</span>
          <input
            type="text"
            value={name}
            maxLength={120}
            onChange={(e) => setName(e.target.value)}
            className={styles.dialogInput}
          />
        </label>

        <div className={styles.dialogField}>
          <span>Tags</span>
          <div className={styles.tagRow}>
            {tags.map((t) => (
              <span key={t} className={styles.tagChip}>
                {t}
                <button
                  type="button"
                  onClick={() => setTags(tags.filter((x) => x !== t))}
                  aria-label={`Remove tag ${t}`}
                >
                  ×
                </button>
              </span>
            ))}
            <input
              type="text"
              value={tagInput}
              placeholder="add a tag…"
              onChange={(e) => setTagInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  addTag();
                }
              }}
              className={styles.tagInput}
            />
          </div>
        </div>

        <label className={styles.dialogField}>
          <span>Format</span>
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value as StashFormat)}
            className={styles.dialogInput}
          >
            {FORMATS.map((f) => (
              <option key={f} value={f}>
                {STASH_FORMAT_LABEL[f]}
              </option>
            ))}
          </select>
        </label>

        <label className={styles.dialogField}>
          <span>Folder</span>
          <select
            value={selectedFolder ?? ''}
            onChange={(e) => setFolder(e.target.value || null)}
            className={styles.dialogInput}
            disabled={foldersQuery.isLoading || folders.length === 0}
            title="Which sample folder this chop is saved to (Settings → Library → Folders → Sample Studio Folders)"
          >
            {foldersQuery.isLoading && <option value="">Loading folders…</option>}
            {!foldersQuery.isLoading && folders.length === 0 && (
              <option value="">Default folder</option>
            )}
            {folders.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>

        <div className={styles.statusLine}>
          {track.title || 'Untitled'} · {formatTime(start)} → {formatTime(end)} (
          {formatTime(Math.max(0, end - start))})
          {fxBits.length > 0 ? ` · ${fxBits.join(' · ')}` : ''}
        </div>

        {error && (
          <div className={styles.statusLine} data-tone="warn">
            {error}
          </div>
        )}

        <div className={styles.dialogActions}>
          <button
            type="button"
            className={styles.transportBtn}
            onClick={audition}
            disabled={auditioning}
          >
            <SpeakerIcon size={14} />
            {auditioning ? 'Rendering…' : 'Audition'}
          </button>
          <span style={{ flex: 1 }} />
          <button type="button" className={styles.transportBtn} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className={styles.transportBtn}
            data-on={true}
            onClick={save}
            disabled={saving || !name.trim()}
          >
            <SaveIcon size={14} />
            {saving ? 'Saving…' : 'Save to stash'}
          </button>
        </div>

        {auditionUrl && <audio ref={audioRef} src={auditionUrl} preload="auto" />}
      </div>
    </div>
  );
}
