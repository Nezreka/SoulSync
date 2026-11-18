/**
 * A duplicate tracks finding: the copies side by side, and Keep Best (#2172).
 *
 * What each copy's playlists mean is read live from the server's own plan
 * for Keep Best: a copy in the same playlists as the kept copy goes without
 * any playlist losing the song, a playlist the user said never protects is
 * named as such, and with moving on a copy's entry in a playlist the kept
 * copy is not in moves to the kept copy. The confirm dialog says the plan
 * for the keeper that was picked.
 */
import { useEffect, useState, type ReactNode } from 'react';

import type { KeepBestPreview } from '../-tools.api';
import type { RepairFinding } from '../-tools.types';

import { fetchKeepBestPreview } from '../-tools.api';
import { copyPlaylistNotes, keepBestSummary } from '../-tools.duplicate-playlists';
import { formatDurationMs } from '../../sync/-sync.server';

interface DuplicateCopy {
  file_id: number;
  track_id: number;
  title?: string;
  artist?: string;
  album?: string;
  file_path?: string;
  format?: string;
  bitrate?: number;
  duration?: number;
  playlists?: string[];
  protected_reasons?: string[];
}

export function NativeDuplicateReview({
  finding,
  onKeepDuplicate,
  protectionLabels,
  media,
  renderPlay,
}: {
  finding: RepairFinding;
  onKeepDuplicate: (findingId: number, trackId: string, message?: string) => void;
  protectionLabels: Record<string, string>;
  media: ReactNode;
  renderPlay: (copy: DuplicateCopy) => ReactNode;
}) {
  const [confirmedRecording, setConfirmedRecording] = useState(false);
  // null follows the job setting until the box is touched
  const [move, setMove] = useState<boolean | null>(null);
  const [preview, setPreview] = useState<KeepBestPreview | null>(null);
  const details = (finding.details || {}) as Record<string, unknown>;
  const tracks = Array.isArray(details.tracks) ? (details.tracks as DuplicateCopy[]) : [];
  const needsConfirmation = Boolean(details.requires_recording_confirmation);
  const approvedRecording = !needsConfirmation || confirmedRecording;
  const flags = `${needsConfirmation && confirmedRecording ? ':confirmed' : ''}${
    move === null ? '' : move ? ':move' : ':nomove'
  }`;
  const valid = details.schema === 'native_duplicate_review/v1' && tracks.length > 0;
  const anyPlaylists = tracks.some((track) => (track.playlists || []).length > 0);

  // what Keep Best does right now: the playlists, the settings, and anything
  // that changed since the scan
  useEffect(() => {
    if (!valid || !approvedRecording) return;
    let alive = true;
    void fetchKeepBestPreview(finding.id, `keep_best${flags}`).then((result) => {
      if (alive) setPreview(result);
    });
    return () => {
      alive = false;
    };
  }, [approvedRecording, finding.id, flags, valid]);

  const rows = new Map(
    (preview?.success ? preview.copies || [] : []).map((row) => [row.file_id, row]),
  );
  const moving = move ?? preview?.move_playlist_entries ?? false;

  const choose = async (action: string) => {
    const full = `${action}${flags}`;
    // the dialog says what this keeper does, from the plan the apply runs
    const planned = await fetchKeepBestPreview(finding.id, full);
    onKeepDuplicate(
      finding.id,
      full,
      planned?.success && planned.copies ? keepBestSummary(planned.copies) : undefined,
    );
  };

  if (!valid) {
    return <p>This duplicate review needs a fresh native scan before it can be applied.</p>;
  }
  return (
    <>
      {media}
      <p>
        Compare the copies, then keep a file. Redundant copies move to recoverable quarantine;
        protected files stay.
      </p>
      {needsConfirmation ? (
        <label>
          <input
            type="checkbox"
            checked={confirmedRecording}
            onChange={(event) => setConfirmedRecording(event.target.checked)}
          />{' '}
          I confirm these files contain the same recording
        </label>
      ) : null}
      {anyPlaylists ? (
        <label className="repair-duplicate-move">
          <input
            type="checkbox"
            checked={moving}
            onChange={(event) => setMove(event.target.checked)}
          />{' '}
          Move playlist entries to the kept copy
          <span className="repair-duplicate-move-note">
            A removed copy&apos;s entry in a playlist the kept copy is not in is pointed at the kept
            copy instead, in the same place. Smart playlists are never changed.
          </span>
        </label>
      ) : null}
      <div className="repair-detail-sublist">
        {tracks.map((track) => {
          const row = rows.get(track.file_id);
          const isBest = row
            ? row.action === 'keep'
            : track.file_id === details.recommended_file_id;
          const protectedReasons = row ? row.protected_reasons : track.protected_reasons || [];
          const protectedCopy = row ? row.action === 'protected' : protectedReasons.length > 0;
          const notes = row ? copyPlaylistNotes(row) : [];
          return (
            <div
              className={`repair-detail-subitem ${isBest ? 'best' : protectedCopy ? 'protected' : 'removable'}`}
              key={track.file_id}
            >
              <div className="repair-subitem-body">
                <strong>
                  <span
                    className={
                      isBest || protectedCopy ? 'repair-keep-badge' : 'repair-remove-badge'
                    }
                  >
                    {isBest ? 'KEEP' : protectedCopy ? 'PROTECTED' : 'QUARANTINE'}
                  </span>{' '}
                  {track.title || ''} by {track.artist || ''}
                </strong>
                <span>
                  Album: {track.album || 'Unknown'}
                  {track.format ? ` · ${track.format.toUpperCase()}` : ''}
                  {track.bitrate ? ` · ${track.bitrate} kbps` : ''}
                  {track.duration ? ` · ${formatDurationMs(track.duration)}` : ''}
                </span>
                {track.file_path ? <span className="mono">{track.file_path}</span> : null}
                {protectedReasons.length ? (
                  <span>
                    Preserved:{' '}
                    {protectedReasons
                      .map((reason) => protectionLabels[reason] || 'Protected copy')
                      .join(', ')}
                  </span>
                ) : null}
                {notes.length ? (
                  notes.map((note) => (
                    <span className="repair-in-playlist" key={note}>
                      {note}
                    </span>
                  ))
                ) : track.playlists?.length ? (
                  <span className="repair-in-playlist">
                    In playlist: {track.playlists.join(', ')}
                  </span>
                ) : null}
                <button
                  type="button"
                  className="repair-btn repair-btn-fix"
                  disabled={!approvedRecording || protectedReasons.includes('file_unavailable')}
                  onClick={() => void choose(`file-${track.file_id}`)}
                >
                  Keep this file
                </button>
              </div>
              {renderPlay(track)}
            </div>
          );
        })}
      </div>
      <button
        type="button"
        className="repair-btn repair-btn-fix"
        disabled={!approvedRecording}
        onClick={() => void choose('keep_best')}
      >
        Keep Best
      </button>
    </>
  );
}
