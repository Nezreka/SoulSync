import { useEffect, useState, type ReactNode, type SVGProps } from 'react';

import type { AlbumAssignment, ReviewCase, ReviewFinding } from '../-review.api';

import { musicBrainzBrowserUrl } from './musicbrainz-links';
import { ProviderReleaseId } from './provider-release-id';
import { ReviewArtwork } from './review-artwork';
import { ReviewAudio } from './review-audio';
import styles from './review-page.module.css';
import { formatTrackPosition } from './review-position';
import {
  record as object,
  records as objects,
  textValue as label,
  changeFieldLabel as proposedFieldLabel,
  displayChanges,
} from './review-presentation';

type Data = Record<string, unknown>;
type Tone = 'good' | 'warning' | 'danger' | 'info' | 'muted';
type IconName = 'check' | 'warning' | 'x' | 'audio' | 'disc' | 'info' | 'arrow';

function count(value: unknown): number {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function baseVersionTitle(value: string): string {
  return value
    .replace(/\.[^.]+$/, '')
    .replace(/^\d+\s*[-_.]\s*/, '')
    .split(/\s+[-–]\s+|\s*\(/, 1)[0]
    .toLocaleLowerCase()
    .trim();
}

function percent(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)}%` : '';
}

function MusicBrainzId({ value, kind }: { value: unknown; kind: 'release' | 'recording' }) {
  const id = label(value);
  if (!id) return null;
  return (
    <a href={musicBrainzBrowserUrl(kind, id)} target="_blank" rel="noreferrer">
      {id}
    </a>
  );
}

function EvidenceIcon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  const path: Record<IconName, ReactNode> = {
    check: <path d="m4 12 5 5L20 6" />,
    warning: (
      <>
        <path d="M12 3 2.8 20h18.4L12 3Z" />
        <path d="M12 9v4.5M12 17h.01" />
      </>
    ),
    x: <path d="m6 6 12 12M18 6 6 18" />,
    audio: <path d="M4 14v-4M8 18V6M12 15V9M16 19V5M20 14v-4" />,
    disc: (
      <>
        <circle cx="12" cy="12" r="9" />
        <circle cx="12" cy="12" r="2.5" />
      </>
    ),
    info: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 11v6M12 7h.01" />
      </>
    ),
    arrow: <path d="M5 12h14M14 7l5 5-5 5" />,
  };
  return (
    <svg
      aria-hidden="true"
      className={styles.evidenceIcon}
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
      {...props}
    >
      {path[name]}
    </svg>
  );
}

export function StatusChip({
  tone,
  icon,
  children,
}: {
  tone: Tone;
  icon: IconName;
  children: ReactNode;
}) {
  return (
    <span className={styles.statusChip} data-tone={tone}>
      <EvidenceIcon name={icon} />
      {children}
    </span>
  );
}

export function CandidateFit({ candidate }: { candidate: Data }) {
  const matched = count(candidate.matched);
  const total = count(candidate.total_slots);
  const missing = Array.isArray(candidate.missing)
    ? candidate.missing.length
    : count(candidate.missing);
  const extra = Array.isArray(candidate.extra) ? candidate.extra.length : count(candidate.extra);
  return (
    <div className={styles.candidateStats} aria-label="Release track fit">
      <StatusChip tone={matched === total && total > 0 ? 'good' : 'warning'} icon="check">
        {matched}/{total || '?'} assigned
      </StatusChip>
      <StatusChip tone={missing ? 'danger' : 'good'} icon={missing ? 'x' : 'check'}>
        {missing} missing
      </StatusChip>
      <StatusChip tone={extra ? 'warning' : 'good'} icon={extra ? 'warning' : 'check'}>
        {extra} extra
      </StatusChip>
    </div>
  );
}

function audioSummary(recording: Data): string {
  return [label(recording.title), label(recording.artist), percent(recording.score)]
    .filter(Boolean)
    .join(' · ');
}

function formatDuration(value: unknown): string {
  const milliseconds = count(value);
  if (!milliseconds) return '';
  const minutes = Math.floor(milliseconds / 60000);
  const seconds = (milliseconds - minutes * 60000) / 1000;
  return `${minutes}:${seconds.toFixed(2).padStart(5, '0')}`;
}

function durationFit(
  pair: Data,
): { label: string; tone: Tone; icon: IconName; detail: string } | null {
  const rawDelta = pair.duration_delta_ms;
  const delta = typeof rawDelta === 'number' && Number.isFinite(rawDelta) ? rawDelta : null;
  if (delta === null) return null;
  const seconds = `${(delta / 1000).toFixed(2)}s`;
  const local = formatDuration(pair.file_duration_ms);
  const release = formatDuration(pair.slot_duration_ms);
  const detail = [local && release ? `${local} ↔ ${release}` : '', `Δ ${seconds}`]
    .filter(Boolean)
    .join(' · ');
  if (delta <= 1000) return { label: 'Exact', tone: 'good', icon: 'check', detail };
  if (delta <= 3000) return { label: 'Close', tone: 'good', icon: 'check', detail };
  if (delta <= 15000) return { label: 'Loose', tone: 'warning', icon: 'warning', detail };
  return { label: 'Conflict', tone: 'danger', icon: 'x', detail };
}

function pairTone(pair: Data): Tone {
  if (label(pair.confidence) === 'ambiguous') return 'warning';
  if (label(pair.confidence) === 'clear') return 'good';
  return 'info';
}

function TrackRows({ pairs }: { pairs: Data[] }) {
  return (
    <div className={styles.trackRows}>
      {pairs.map((pair, index) => {
        const tone = pairTone(pair);
        const duration = durationFit(pair);
        const ambiguity = Array.isArray(pair.ambiguity_reasons)
          ? pair.ambiguity_reasons.map(label).filter(Boolean)
          : [];
        const audioAlternatives = objects(pair.audio_alternatives);
        const alternativeNames = audioAlternatives
          .map((recording) => {
            const name = label(recording.title) || 'unnamed recording';
            const linked = count(recording.recording_count);
            return linked > 1 ? `${name} (${linked} IDs)` : name;
          })
          .slice(0, 2);
        const playlists = Array.isArray(pair.playlists)
          ? pair.playlists.map(label).filter(Boolean)
          : [];
        const reasons = Array.isArray(pair.reasons) ? pair.reasons.map(label).filter(Boolean) : [];
        const localTitle = label(pair.tag_title) || label(pair.file) || 'Unknown file';
        const releaseTitle = label(pair.slot_title) || 'Unknown release track';
        const localDisambiguation = label(pair.local_recording_disambiguation);
        const slotDisambiguation = label(pair.slot_recording_disambiguation);
        return (
          <article
            className={styles.trackRow}
            data-tone={tone}
            key={`${label(pair.file)}-${label(pair.slot_number)}-${index}`}
          >
            <div className={styles.trackStatus} title={label(pair.confidence) || 'Uncertain'}>
              <EvidenceIcon
                name={tone === 'good' ? 'check' : tone === 'warning' ? 'warning' : 'info'}
              />
            </div>
            <div className={styles.trackNumber}>
              {pair.slot_number
                ? `${label(pair.disc_number || 1)}.${label(pair.slot_number)}`
                : '—'}
            </div>
            <div className={styles.trackIdentity}>
              <div>
                <small>Local</small>
                <strong>{localTitle}</strong>
                {label(pair.file) && label(pair.file) !== localTitle && (
                  <span>{label(pair.file)}</span>
                )}
                {localDisambiguation && <span>Recording: {localDisambiguation}</span>}
                {playlists.length > 0 && (
                  <span className={styles.playlistLine}>Playlist: {playlists.join(', ')}</span>
                )}
              </div>
              <EvidenceIcon name="arrow" />
              <div>
                <small>Release</small>
                <strong>{releaseTitle}</strong>
                {slotDisambiguation && <span>Recording: {slotDisambiguation}</span>}
                {Boolean(pair.audio_title) && (
                  <span className={styles.audioLine}>
                    <EvidenceIcon name="audio" /> {label(pair.audio_title)}
                    {Boolean(pair.audio_score) && ` · ${percent(pair.audio_score)}`}
                    {count(pair.audio_recording_count) > 1 &&
                      ` · ${count(pair.audio_recording_count)} catalog IDs`}
                  </span>
                )}
                {audioAlternatives.length > 0 && (
                  <span
                    className={styles.audioAlternatives}
                    title={audioAlternatives
                      .map((recording) => audioSummary(recording) || 'Unnamed recording')
                      .join('\n')}
                  >
                    <EvidenceIcon name="warning" />
                    {tone === 'good' ? 'AcoustID alternate ruled out: ' : 'AcoustID also links '}
                    {alternativeNames.join(', ')}
                    {audioAlternatives.length > alternativeNames.length &&
                      ` +${audioAlternatives.length - alternativeNames.length}`}
                  </span>
                )}
              </div>
            </div>
            <div className={styles.trackEvidence}>
              {duration && (
                <StatusChip tone={duration.tone} icon={duration.icon}>
                  {duration.label}
                </StatusChip>
              )}
              {duration && <small>{duration.detail}</small>}
              {ambiguity.map((reason) => (
                <span className={styles.reason} key={reason}>
                  {reason}
                </span>
              ))}
              {ambiguity.length === 0 && reasons.length > 0 && <small>{reasons.join(' · ')}</small>}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function TrackGroups({ pairs }: { pairs: Data[] }) {
  const attention = pairs.filter((pair) => label(pair.confidence) !== 'clear');
  const confirmed = pairs.filter((pair) => label(pair.confidence) === 'clear');
  return (
    <div className={styles.trackGroups}>
      {attention.length > 0 && (
        <section className={styles.attentionGroup}>
          <h5>
            <EvidenceIcon name="warning" /> Needs attention
            <span>{attention.length}</span>
          </h5>
          <TrackRows pairs={attention} />
        </section>
      )}
      {confirmed.length > 0 && (
        <details className={styles.confirmedGroup}>
          <summary>
            <EvidenceIcon name="check" />
            {confirmed.length} confirmed match
            {confirmed.length === 1 ? '' : 'es'}
            <span>Show</span>
          </summary>
          <TrackRows pairs={confirmed} />
        </details>
      )}
    </div>
  );
}

function EvidenceSummary({ details }: { details: Data }) {
  const candidate = objects(details.candidates)[0];
  if (!candidate) return null;
  const pairs = objects(candidate.pairs);
  const matched = count(candidate.matched);
  const total = count(candidate.total_slots) || pairs.length;
  const clear = count(candidate.clear);
  const missing = Array.isArray(candidate.missing) ? candidate.missing.length : 0;
  const extra = Array.isArray(candidate.extra) ? candidate.extra.length : 0;
  const audio = pairs.filter((pair) => Boolean(pair.audio_title || pair.audio_score)).length;
  const ambiguous = Math.max(0, matched - clear);
  const playlistNames = new Set(
    pairs.flatMap((pair) =>
      Array.isArray(pair.playlists) ? pair.playlists.map(label).filter(Boolean) : [],
    ),
  );
  return (
    <section className={styles.summaryStrip} aria-label="Evidence summary">
      <StatusChip tone={matched === total && total > 0 ? 'good' : 'warning'} icon="disc">
        {matched}/{total || '?'} assigned
      </StatusChip>
      <StatusChip tone={audio === pairs.length && pairs.length > 0 ? 'good' : 'info'} icon="audio">
        {audio}/{pairs.length || '?'} audio IDs
      </StatusChip>
      <StatusChip tone={missing ? 'danger' : 'good'} icon={missing ? 'x' : 'check'}>
        {missing} missing
      </StatusChip>
      <StatusChip tone={extra ? 'warning' : 'good'} icon={extra ? 'warning' : 'check'}>
        {extra} unassigned
      </StatusChip>
      {ambiguous > 0 && (
        <StatusChip tone="warning" icon="warning">
          {ambiguous} uncertain
        </StatusChip>
      )}
      {playlistNames.size > 0 && (
        <StatusChip tone="info" icon="info">
          In {playlistNames.size} playlist{playlistNames.size === 1 ? '' : 's'}
        </StatusChip>
      )}
    </section>
  );
}

function DuplicateCopies({
  details,
  onKeepCopy,
  busy,
}: {
  details: Data;
  onKeepCopy?: (trackId: string) => void;
  busy: boolean;
}) {
  const copies = objects(details.duplicate_copies);
  if (copies.length < 2) return null;
  return (
    <section className={styles.duplicateCard}>
      <div className={styles.sectionHeading}>
        <div>
          <h4>
            <EvidenceIcon name="warning" /> Duplicate local copies
          </h4>
          <p>These files identify as the same track. Choose the copy SoulSync should keep.</p>
        </div>
      </div>
      <div className={styles.duplicateList}>
        {copies.map((copy, index) => {
          const path = label(copy.file_path);
          const playlists = Array.isArray(copy.playlists)
            ? copy.playlists.map(label).filter(Boolean)
            : [];
          const trackId = label(copy.track_id || copy.id);
          const quality = object(copy.quality);
          const audioFacts = [
            label(quality.format),
            quality.sample_rate ? `${Math.round(count(quality.sample_rate) / 1000)} kHz` : '',
            quality.bits_per_sample ? `${label(quality.bits_per_sample)} bit` : '',
            quality.bitrate || copy.bitrate
              ? `${Math.round(count(quality.bitrate || copy.bitrate) / 1000)} kbps`
              : '',
            copy.duration ? formatDuration(copy.duration) : '',
            copy.size_bytes ? `${(count(copy.size_bytes) / 1048576).toFixed(1)} MB` : '',
          ].filter(Boolean);
          return (
            <article key={`${trackId}-${path}-${index}`}>
              <div>
                <strong>{path.split('/').pop() || label(copy.title) || 'Unknown file'}</strong>
                <small>{path}</small>
                {audioFacts.length > 0 && (
                  <span className={styles.audioFacts}>{audioFacts.join(' · ')}</span>
                )}
                {Boolean(copy.recording_disambiguation) && (
                  <span>Recording: {label(copy.recording_disambiguation)}</span>
                )}
                {playlists.length > 0 && <span>Playlist: {playlists.join(', ')}</span>}
              </div>
              {onKeepCopy && trackId && (
                <button type="button" disabled={busy} onClick={() => onKeepCopy(trackId)}>
                  Keep this copy
                </button>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function AudioEvidence({ details }: { details: Data }) {
  const audio = object(details.acoustid);
  const grouped = new Map<string, Data>();
  for (const recording of objects(audio.recordings)) {
    const identity = `${label(recording.title)
      .toLocaleLowerCase()
      .replaceAll(/[^\p{L}\p{N}\s]/gu, ' ')
      .replaceAll(/\s+/g, ' ')
      .trim()}::${label(recording.artist)
      .toLocaleLowerCase()
      .replaceAll(/[^\p{L}\p{N}\s]/gu, ' ')
      .replaceAll(/\s+/g, ' ')
      .trim()}`;
    const current = grouped.get(identity);
    if (!current || count(recording.score) > count(current.score)) {
      grouped.set(identity, {
        ...recording,
        recording_count: count(current?.recording_count) + 1,
      });
    } else {
      current.recording_count = count(current.recording_count) + 1;
    }
  }
  const recordings = [...grouped.values()].sort((a, b) => count(b.score) - count(a.score));
  const status = label(audio.status || details.acoustid_status);
  if (!status) return null;
  return (
    <section className={styles.technicalCard}>
      <h5>
        <EvidenceIcon name="audio" /> Audio identity
      </h5>
      <p>
        {status === 'ok'
          ? `${recordings.length} AcoustID candidate${recordings.length === 1 ? '' : 's'}`
          : status === 'no_match'
            ? 'No AcoustID match'
            : `AcoustID status: ${status.replaceAll('_', ' ')}`}
      </p>
      {recordings.length > 0 && (
        <ol className={styles.audioCandidates}>
          {recordings.slice(0, 5).map((recording, index) => (
            <li key={`${label(recording.mbid)}-${index}`}>
              <strong>{audioSummary(recording) || 'Unnamed recording'}</strong>
              {count(recording.recording_count) > 1 && (
                <small>{count(recording.recording_count)} linked recording IDs</small>
              )}
              {Boolean(recording.mbid) && (
                <small>
                  Recording ID: <MusicBrainzId value={recording.mbid} kind="recording" />
                </small>
              )}
            </li>
          ))}
        </ol>
      )}
      {recordings.length > 5 && <small>{recordings.length - 5} more candidates</small>}
    </section>
  );
}

function candidateAssignments(candidate: Data): string {
  const pairs = objects(candidate.pairs)
    .map(
      (pair) =>
        `${label(pair.file)}:${label(pair.disc_number)}:${label(pair.track_number || pair.slot_number)}:${label(pair.slot_title)}`,
    )
    .sort()
    .join('|');
  const missing = Array.isArray(candidate.missing)
    ? candidate.missing.map(label).join('|')
    : Array.isArray(candidate.missing_tracks)
      ? candidate.missing_tracks.map(label).join('|')
      : '';
  const extra = Array.isArray(candidate.extra)
    ? candidate.extra.map(label).join('|')
    : Array.isArray(candidate.extra_files)
      ? candidate.extra_files.map(label).join('|')
      : '';
  return `${label(candidate.total_slots)}::${pairs}::missing=${missing}::extra=${extra}`;
}

export function ReleaseComparison({
  candidates,
  selected,
  onSelect,
  onPin,
  busy = false,
  currentRelease,
  fallbackArtwork,
  numberingMode,
  view = 'all',
}: {
  candidates: Data[];
  selected: number;
  onSelect: (index: number) => void;
  onPin?: (index: number) => void;
  busy?: boolean;
  currentRelease?: Data;
  fallbackArtwork?: string;
  numberingMode?: string;
  view?: 'all' | 'list' | 'detail';
}) {
  const [inspectedPair, setInspectedPair] = useState<number | null>(null);
  useEffect(() => setInspectedPair(null), [selected]);
  if (!candidates.length) return null;
  const active = candidates[Math.min(selected, candidates.length - 1)];
  const pairs = objects(active.pairs).sort(
    (left, right) =>
      count(left.disc_number) - count(right.disc_number) ||
      count(left.track_number || left.slot_number) - count(right.track_number || right.slot_number),
  );
  const missing = Array.isArray(active.missing)
    ? active.missing.map(label)
    : Array.isArray(active.missing_tracks)
      ? active.missing_tracks.map(label)
      : [];
  const extra = Array.isArray(active.extra)
    ? active.extra.map(label)
    : Array.isArray(active.extra_files)
      ? active.extra_files.map(label)
      : [];
  const possibleVersionPairs = missing.flatMap((releaseName) =>
    extra
      .filter((file) => baseVersionTitle(file) === baseVersionTitle(releaseName))
      .map((file) => ({ releaseName, file })),
  );
  const firstShape = candidateAssignments(candidates[0]);
  const allEquivalent = candidates.every(
    (candidate) => candidateAssignments(candidate) === firstShape,
  );
  return (
    <section className={styles.releaseSection} aria-label="Compare release candidates">
      {view !== 'detail' && (
        <>
          <div className={styles.sectionHeading}>
            <div>
              <h4>
                <EvidenceIcon name="disc" /> Compare {candidates.length} release candidates
              </h4>
              <p>
                {allEquivalent
                  ? 'These editions assign the available files the same way. Choose by country, format, date, or edition.'
                  : 'Select an edition to inspect its track mapping and unmatched files.'}
              </p>
            </div>
          </div>
          <div className={styles.releaseSelector}>
            {candidates.map((candidate, index) => {
              const isCurrent =
                Boolean(currentRelease?.release_id) &&
                currentRelease?.source === candidate.source &&
                String(currentRelease?.release_id) === String(candidate.release_id);
              return (
                <div
                  className={styles.candidateCard}
                  data-selected={selected === index || undefined}
                  key={`${label(candidate.source)}-${label(candidate.release_id)}-${index}`}
                >
                  <button
                    type="button"
                    data-selected={selected === index || undefined}
                    onClick={() => onSelect(index)}
                  >
                    <span className={styles.candidateRank}>{index + 1}</span>
                    <ReviewArtwork
                      title={label(candidate.release_title) || 'Release'}
                      url={label(candidate.image_url)}
                      fallbackUrl={fallbackArtwork}
                      size="release"
                    />
                    <span>
                      <strong>{label(candidate.release_title) || `Candidate ${index + 1}`}</strong>
                      <small>
                        {[
                          candidate.source,
                          candidate.country,
                          candidate.format,
                          candidate.date,
                          candidate.disambiguation,
                        ]
                          .map(label)
                          .filter(Boolean)
                          .join(' · ')}
                      </small>
                      {isCurrent && <small>Current library release</small>}
                    </span>
                    <CandidateFit candidate={candidate} />
                  </button>
                  {selected === index && onPin && (
                    <button
                      className={styles.candidateUse}
                      type="button"
                      disabled={busy}
                      onClick={() => onPin(index)}
                    >
                      Use this release
                    </button>
                  )}
                </div>
              );
            })}
          </div>
          {fallbackArtwork && (
            <p className={styles.muted}>
              Candidates without catalog artwork show the current library cover.
            </p>
          )}
        </>
      )}
      {view !== 'list' && (
        <div className={styles.releaseDetail}>
          <div className={styles.releaseDetailHeader}>
            <div>
              <strong>{label(active.release_title) || `Candidate ${selected + 1}`}</strong>
              <small>
                {label(active.source)}{' '}
                <ProviderReleaseId
                  source={label(active.source)}
                  releaseId={label(active.release_id)}
                />
              </small>
            </div>
          </div>
          {pairs.length > 0 ? (
            <div className={styles.candidateTrackList}>
              {pairs.map((pair, index) => (
                <div key={`${label(pair.file)}-${index}`}>
                  <b>
                    {formatTrackPosition(
                      label(pair.disc_number || 1),
                      label(pair.track_number || pair.slot_number),
                      numberingMode,
                      label(pair.display_track_number) || undefined,
                    )}
                  </b>
                  <span>
                    <strong>{label(pair.slot_title)}</strong>
                    <small>
                      {label(pair.file)}
                      {pair.file_duration_ms ? ` · ${formatDuration(pair.file_duration_ms)}` : ''}
                    </small>
                    {label(pair.confidence) !== 'clear' && (
                      <small className={styles.reason}>
                        {Array.isArray(pair.ambiguity_reasons) && pair.ambiguity_reasons.length
                          ? label(pair.ambiguity_reasons[0])
                          : 'The assignment has a competing match; inspect the evidence.'}
                      </small>
                    )}
                  </span>
                  <StatusChip
                    tone={label(pair.confidence) === 'clear' ? 'good' : 'warning'}
                    icon={label(pair.confidence) === 'clear' ? 'check' : 'warning'}
                  >
                    {label(pair.confidence) === 'clear' ? 'Strong match' : 'Needs review'}
                  </StatusChip>
                  {label(pair.confidence) !== 'clear' && (
                    <button type="button" onClick={() => setInspectedPair(index)}>
                      Inspect match
                    </button>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className={styles.muted}>No local files could be assigned to this release.</p>
          )}
          {inspectedPair !== null && pairs[inspectedPair] && (
            <div className={styles.candidateInspection}>
              <h5>Candidate track evidence</h5>
              <TrackRows pairs={[pairs[inspectedPair]]} />
              {Boolean(pairs[inspectedPair].file_path) && (
                <ReviewAudio
                  localPath={label(pairs[inspectedPair].file_path)}
                  localTitle={label(pairs[inspectedPair].local_title || pairs[inspectedPair].file)}
                  localDurationMs={count(pairs[inspectedPair].file_duration_ms)}
                  comparisonTitle={label(pairs[inspectedPair].slot_title)}
                  comparisonDurationMs={count(pairs[inspectedPair].slot_duration_ms)}
                  previewUrl={label(pairs[inspectedPair].slot_preview_url)}
                  previewSource={label(active.source)}
                  previewTrackId={label(pairs[inspectedPair].slot_source_track_id)}
                />
              )}
              <p className={styles.muted}>
                Choose this release to create its actionable track review.
              </p>
            </div>
          )}
          {(missing.length > 0 || extra.length > 0) && (
            <div>
              {possibleVersionPairs.length > 0 && (
                <div className={styles.versionPairNotice}>
                  <strong>Possible version difference</strong>
                  {possibleVersionPairs.map(({ releaseName, file }) => (
                    <span key={`${releaseName}-${file}`}>
                      {file} may be a different version of the release track “{releaseName}”.
                    </span>
                  ))}
                </div>
              )}
              <div className={styles.candidateExceptions}>
                {missing.length > 0 && (
                  <section>
                    <strong>
                      {missing.length} absent release track{missing.length === 1 ? '' : 's'}
                    </strong>
                    <ul>
                      {missing.map((name, index) => (
                        <li key={`${name}-${index}`}>{name}</li>
                      ))}
                    </ul>
                  </section>
                )}
                {extra.length > 0 && (
                  <section>
                    <strong>
                      {extra.length} unassigned local file{extra.length === 1 ? '' : 's'}
                    </strong>
                    <ul>
                      {extra.map((name, index) => (
                        <li key={`${name}-${index}`}>{name}</li>
                      ))}
                    </ul>
                  </section>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function CheckedFiles({ details }: { details: Data }) {
  const files = objects(details.checked_files);
  if (!files.length) return null;
  return (
    <section className={styles.technicalCard}>
      <h5>Files checked</h5>
      <ul className={styles.checkedFiles}>
        {files.map((file, index) => (
          <li key={`${label(file.file)}-${index}`}>
            <EvidenceIcon name={file.audio_title ? 'check' : 'warning'} />
            <span>
              <strong>{label(file.file)}</strong>
              <small>
                {audioSummary({
                  title: file.audio_title,
                  artist: file.audio_artist,
                  score: file.audio_score,
                }) ||
                  label(file.audio_status) ||
                  'No match'}
              </small>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Facts({ finding, details }: { finding: Pick<ReviewFinding, 'file_path'>; details: Data }) {
  const tags = object(details.current_tags);
  const currentTitle = label(tags.title || details.current_title);
  const replacement = object(details.replacement_track);
  const expectedTitle = label(details.expected_title || replacement.title || replacement.name);
  const currentDisambiguation = label(details.current_recording_disambiguation);
  const expectedDisambiguation = label(details.expected_recording_disambiguation);
  const folder = label(details.album_folder);
  return (
    <div className={styles.evidenceColumns}>
      <section className={styles.technicalCard}>
        <h5>Local evidence</h5>
        <dl className={styles.factList}>
          {finding.file_path && (
            <div>
              <dt>File</dt>
              <dd>{finding.file_path.split('/').pop()}</dd>
            </div>
          )}
          {folder && (
            <div>
              <dt>Folder</dt>
              <dd>{folder}</dd>
            </div>
          )}
          {Boolean(details.file_duration_ms || tags.duration_ms) && (
            <div>
              <dt>Length</dt>
              <dd>{formatDuration(details.file_duration_ms || tags.duration_ms)}</dd>
            </div>
          )}
          {currentTitle && (
            <div>
              <dt>Title</dt>
              <dd>{currentTitle}</dd>
            </div>
          )}
          {currentDisambiguation && (
            <div>
              <dt>Recording</dt>
              <dd>{currentDisambiguation}</dd>
            </div>
          )}
          {Boolean(tags.artist) && (
            <div>
              <dt>Artist</dt>
              <dd>{label(tags.artist)}</dd>
            </div>
          )}
          {Boolean(tags.album) && (
            <div>
              <dt>Album</dt>
              <dd>{label(tags.album)}</dd>
            </div>
          )}
          {!finding.file_path && details.file_count !== undefined && (
            <div>
              <dt>Files checked</dt>
              <dd>{label(details.file_count)}</dd>
            </div>
          )}
        </dl>
      </section>
      <section className={styles.technicalCard}>
        <h5>Release track</h5>
        <dl className={styles.factList}>
          {expectedTitle && (
            <div>
              <dt>Title</dt>
              <dd>{expectedTitle}</dd>
            </div>
          )}
          {Boolean(details.expected_duration_ms || replacement.duration_ms) && (
            <div>
              <dt>Length</dt>
              <dd>{formatDuration(details.expected_duration_ms || replacement.duration_ms)}</dd>
            </div>
          )}
          {Boolean(details.album_title) && (
            <div>
              <dt>Album</dt>
              <dd>{label(details.album_title)}</dd>
            </div>
          )}
          {Boolean(details.release_disambiguation) && (
            <div>
              <dt>Edition</dt>
              <dd>{label(details.release_disambiguation)}</dd>
            </div>
          )}
          {Boolean(details.expected_artist || replacement.artist || details.artist_name) && (
            <div>
              <dt>Track artist</dt>
              <dd>{label(details.expected_artist || replacement.artist || details.artist_name)}</dd>
            </div>
          )}
          {expectedDisambiguation && (
            <div>
              <dt>Recording</dt>
              <dd>{expectedDisambiguation}</dd>
            </div>
          )}
          {Boolean(details.source) && (
            <div>
              <dt>Source</dt>
              <dd>{label(details.source)}</dd>
            </div>
          )}
          {Boolean(details.release_id) && (
            <div>
              <dt>Release ID</dt>
              <dd>
                <ProviderReleaseId
                  source={label(details.source)}
                  releaseId={label(details.release_id)}
                />
              </dd>
            </div>
          )}
          {details.local_file_count !== undefined && details.release_track_count !== undefined && (
            <div>
              <dt>Coverage</dt>
              <dd>
                {label(details.local_file_count)} files / {label(details.release_track_count)}{' '}
                tracks
              </dd>
            </div>
          )}
        </dl>
      </section>
    </div>
  );
}

export function AssignmentEvidence({ row, album }: { row: AlbumAssignment; album: ReviewCase }) {
  const details: Data = {
    current_tags: row.current_tags || { title: row.current_title },
    current_title: row.current_title,
    expected_title: row.slot_title,
    file_duration_ms: row.file_duration_ms,
    expected_duration_ms: row.slot_duration_ms,
    album_folder: row.file_path?.split('/').slice(0, -1).join('/') || album.folder,
    album_title: album.album,
    expected_artist: row.slot_artist,
    source: album.album_plan?.source,
    release_id: album.album_plan?.release_id,
    acoustid: row.acoustid,
  };
  return (
    <section className={styles.findingDetail}>
      <h3>{row.current_title || row.slot_title || 'Track assignment'}</h3>
      <p>
        {row.status === 'confirmed'
          ? 'Confirmed match · no metadata edit is pending.'
          : row.status === 'not_owned'
            ? 'This track is intentionally not collected.'
            : 'No pending action for this assignment.'}
      </p>
      <ReviewAudio
        localPath={row.file_path || ''}
        localTitle={row.current_title || ''}
        localDurationMs={row.file_duration_ms || 0}
        comparisonTitle={row.slot_title || ''}
        comparisonDurationMs={row.slot_duration_ms || 0}
        previewUrl={row.slot_preview_url || ''}
        previewSource={album.album_plan?.source || ''}
      />
      <Facts finding={{ file_path: row.file_path || null }} details={details} />
      {row.acoustid && <AudioEvidence details={details} />}
      {Boolean(row.reasons?.length) && (
        <section className={styles.technicalCard}>
          <h5>Why this track was paired</h5>
          <p>{row.reasons?.join(' · ')}</p>
        </section>
      )}
    </section>
  );
}

export function ReconciliationEvidence({
  finding,
  onKeepCopy,
  busy = false,
  prominent = false,
}: {
  finding: ReviewFinding;
  onKeepCopy?: (trackId: string) => void;
  busy?: boolean;
  prominent?: boolean;
}) {
  const details = object(finding.details);
  const changes = object(details.changes);
  const shownChanges = displayChanges(changes);
  const missing = objects(details.missing_tracks);
  const reasons = Array.isArray(details.match_reasons) ? details.match_reasons.map(label) : [];
  const ambiguityReasons = Array.isArray(details.ambiguity_reasons)
    ? details.ambiguity_reasons.map(label).filter(Boolean)
    : [];
  const primaryPairs = objects(objects(details.candidates)[0]?.pairs);
  const duplicateCopies = objects(details.duplicate_copies);
  const audio = object(details.acoustid);
  const expectedIdentity = `${label(details.expected_title)
    .toLocaleLowerCase()
    .trim()}::${label(details.artist_name).toLocaleLowerCase().trim()}`;
  const bestAudioScore =
    count(audio.best_score) ||
    Math.max(0, ...objects(audio.recordings).map((recording) => count(recording.score)));
  const competingAudioIdentities = [
    ...new Map(
      objects(audio.recordings)
        .filter((recording) => Math.abs(count(recording.score) - bestAudioScore) < 0.000_001)
        .filter((recording) => {
          const identity = `${label(recording.title).toLocaleLowerCase().trim()}::${label(
            recording.artist,
          )
            .toLocaleLowerCase()
            .trim()}`;
          return identity !== expectedIdentity;
        })
        .map((recording) => [
          `${label(recording.title).toLocaleLowerCase()}::${label(recording.artist).toLocaleLowerCase()}`,
          recording,
        ]),
    ).values(),
  ];
  const localDuration = count(details.file_duration_ms || object(details.current_tags).duration_ms);
  const expectedDuration = count(details.expected_duration_ms);
  const durationDelta =
    localDuration && expectedDuration ? Math.abs(localDuration - expectedDuration) : 0;
  const expectedTitle = label(
    details.expected_title ||
      object(details.replacement_track).title ||
      object(details.replacement_track).name,
  )
    .toLocaleLowerCase()
    .trim();
  const audioSupportsExpected = objects(audio.recordings).some(
    (recording) => baseVersionTitle(label(recording.title)) === baseVersionTitle(expectedTitle),
  );

  return (
    <div className={styles.reconciliationEvidence}>
      <EvidenceSummary details={details} />
      {primaryPairs.length > 0 && (
        <section className={styles.trackSection}>
          <div className={styles.sectionHeading}>
            <h4>Track matching</h4>
            <span>Best fitting release</span>
          </div>
          <TrackGroups pairs={primaryPairs} />
        </section>
      )}
      {Object.keys(changes).length > 0 && (
        <section className={styles.changesCard}>
          <h4>
            <EvidenceIcon name="check" /> Proposed changes
          </h4>
          <div className={styles.changeList}>
            {shownChanges.map((change) => {
              return (
                <div key={`${change.field}-${change.old}-${change.next}`}>
                  <strong>{proposedFieldLabel(change.field, change.locations)}</strong>
                  <span className={styles.oldValue}>{change.old || '—'}</span>
                  <EvidenceIcon name="arrow" />
                  <span className={styles.newValue}>{change.next || '—'}</span>
                </div>
              );
            })}
          </div>
        </section>
      )}
      <DuplicateCopies details={details} onKeepCopy={onKeepCopy} busy={busy} />
      {ambiguityReasons.length > 0 && (
        <section className={styles.problemCard} data-tone="warning">
          <h4>
            <EvidenceIcon name="warning" /> Why this needs review
          </h4>
          {durationDelta > 3000 && (
            <p>
              <strong>Length mismatch:</strong> {formatDuration(localDuration)} local versus{' '}
              {formatDuration(expectedDuration)} on the release ({(durationDelta / 1000).toFixed(1)}{' '}
              seconds apart).
            </p>
          )}
          <p>{ambiguityReasons.join(' · ')}</p>
          {competingAudioIdentities.length > 0 && (
            <div className={styles.competingIdentities}>
              <strong>Other recordings linked to this fingerprint</strong>
              <small>
                These scores measure fingerprint similarity, not certainty about the track.
              </small>
              <ul>
                {competingAudioIdentities.slice(0, 3).map((recording) => (
                  <li key={`${label(recording.title)}-${label(recording.artist)}`}>
                    {audioSummary(recording)}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
      {missing.length > 0 && (
        <section className={styles.problemCard} data-tone="danger">
          <h4>
            <EvidenceIcon name="x" /> Tracks absent from this folder
          </h4>
          <ol>
            {missing.map((track, index) => (
              <li key={`${label(track.title || track.name)}-${index}`}>
                {label(track.title || track.name)}
              </li>
            ))}
          </ol>
        </section>
      )}
      {duplicateCopies.length === 0 &&
        Array.isArray(details.competing_copies) &&
        details.competing_copies.length > 0 && (
          <section className={styles.problemCard} data-tone="warning">
            <h4>
              <EvidenceIcon name="warning" /> Other files that fit this slot
            </h4>
            <ul>
              {details.competing_copies.map((path, index) => (
                <li key={index}>{label(path)}</li>
              ))}
            </ul>
          </section>
        )}
      {prominent && (
        <div className={styles.prominentEvidence}>
          {label(audio.status) === 'ok' && expectedTitle && !audioSupportsExpected && (
            <section className={styles.problemCard} data-tone="warning">
              <h4>
                <EvidenceIcon name="warning" /> No AcoustID candidate names this release track
              </h4>
              <p>
                The fingerprint returned other recording identities. Compare the audio before
                choosing this assignment.
              </p>
            </section>
          )}
          <Facts finding={finding} details={details} />
          <AudioEvidence details={details} />
        </div>
      )}
      {prominent && reasons.length > 0 && (
        <section className={styles.technicalCard}>
          <h5>Why this track was paired</h5>
          <p>{reasons.join(' · ')}</p>
          {Boolean(details.confidence) && <p>Confidence: {label(details.confidence)}</p>}
        </section>
      )}
      {(!prominent || objects(details.checked_files).length > 0) && (
        <details className={styles.technicalEvidence} open>
          <summary>
            <EvidenceIcon name="info" /> Technical evidence
            <span>Raw files, tags, and IDs</span>
          </summary>
          <div className={styles.technicalGrid}>
            {!prominent && <Facts finding={finding} details={details} />}
            {!prominent && <AudioEvidence details={details} />}
            <CheckedFiles details={details} />
            {!prominent && reasons.length > 0 && (
              <section className={styles.technicalCard}>
                <h5>Why this track was paired</h5>
                <p>{reasons.join(' · ')}</p>
                {Boolean(details.confidence) && <p>Confidence: {label(details.confidence)}</p>}
              </section>
            )}
          </div>
        </details>
      )}
      {finding.last_error && <p className={styles.error}>Last attempt: {finding.last_error}</p>}
    </div>
  );
}
