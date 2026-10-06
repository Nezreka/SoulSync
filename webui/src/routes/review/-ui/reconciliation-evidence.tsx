import type { ReactNode, SVGProps } from 'react';

import type { ReviewFinding } from '../-review.api';

import styles from './review-page.module.css';

type Data = Record<string, unknown>;
type Tone = 'good' | 'warning' | 'danger' | 'info' | 'muted';
type IconName = 'check' | 'warning' | 'x' | 'audio' | 'disc' | 'info' | 'arrow';

function object(value: unknown): Data {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Data) : {};
}

function objects(value: unknown): Data[] {
  return Array.isArray(value) ? value.map(object).filter((item) => Object.keys(item).length) : [];
}

function label(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

function count(value: unknown): number {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function percent(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)}%` : '';
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

function StatusChip({ tone, icon, children }: { tone: Tone; icon: IconName; children: ReactNode }) {
  return (
    <span className={styles.statusChip} data-tone={tone}>
      <EvidenceIcon name={icon} />
      {children}
    </span>
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
            {confirmed.length} confirmed match{confirmed.length === 1 ? '' : 'es'}
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
          return (
            <article key={`${trackId}-${path}-${index}`}>
              <div>
                <strong>{path.split('/').pop() || label(copy.title) || 'Unknown file'}</strong>
                <small>{path}</small>
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
              {Boolean(recording.mbid) && <small>Recording ID: {label(recording.mbid)}</small>}
            </li>
          ))}
        </ol>
      )}
      {recordings.length > 5 && <small>{recordings.length - 5} more candidates</small>}
    </section>
  );
}

function candidateAssignments(candidate: Data): string {
  return objects(candidate.pairs)
    .map(
      (pair) =>
        `${label(pair.file)}:${label(pair.disc_number)}:${label(pair.slot_number)}:${label(pair.slot_title)}`,
    )
    .join('|');
}

function ReleaseCandidates({
  details,
  onPin,
  busy,
}: {
  details: Data;
  onPin?: (index: number) => void;
  busy: boolean;
}) {
  const candidates = objects(details.candidates);
  const attempted = objects(details.attempted_releases);
  if (!candidates.length && !attempted.length) return null;
  const comparable =
    candidates.length > 1 &&
    candidates.every((candidate) => Array.isArray(candidate.pairs)) &&
    candidates.some((candidate) => objects(candidate.pairs).length > 0);
  const sameAssignments =
    comparable &&
    candidates.every(
      (candidate) => candidateAssignments(candidate) === candidateAssignments(candidates[0]),
    );
  const releases = candidates.length ? candidates : attempted;
  return (
    <section className={styles.releaseSection}>
      <div className={styles.sectionHeading}>
        <div>
          <h4>
            <EvidenceIcon name="disc" />
            {candidates.length ? 'Compared releases' : 'Release lookups attempted'}
          </h4>
          {candidates.length > 1 && (
            <p>
              {sameAssignments
                ? 'Track assignments are identical; audio cannot identify the edition.'
                : 'The releases assign one or more files differently.'}
            </p>
          )}
        </div>
      </div>
      <div className={styles.releaseGrid}>
        {releases.map((candidate, index) => {
          const pairs = objects(candidate.pairs);
          const missing = Array.isArray(candidate.missing) ? candidate.missing.map(label) : [];
          const extra = Array.isArray(candidate.extra) ? candidate.extra.map(label) : [];
          const total = count(candidate.total_slots);
          return (
            <article
              className={styles.releaseCard}
              key={`${label(candidate.source)}-${label(candidate.release_id)}-${index}`}
            >
              <header>
                <span className={styles.candidateRank}>{index + 1}</span>
                <div>
                  <strong>
                    Candidate {index + 1} · {label(candidate.source) || 'Unknown source'}
                  </strong>
                  <small>{label(candidate.release_id)}</small>
                </div>
              </header>
              {candidate.matched !== undefined && (
                <div className={styles.candidateStats}>
                  <StatusChip
                    tone={count(candidate.matched) === total ? 'good' : 'warning'}
                    icon="check"
                  >
                    {count(candidate.matched)}/{total || '?'} assigned
                  </StatusChip>
                  <StatusChip
                    tone={missing.length ? 'danger' : 'good'}
                    icon={missing.length ? 'x' : 'check'}
                  >
                    {missing.length} missing
                  </StatusChip>
                  <StatusChip
                    tone={extra.length ? 'warning' : 'good'}
                    icon={extra.length ? 'warning' : 'check'}
                  >
                    {extra.length} extra
                  </StatusChip>
                </div>
              )}
              {sameAssignments && pairs.length > 0 && (
                <p className={styles.sameMapping}>
                  <EvidenceIcon name="check" /> Same track mapping
                </p>
              )}
              {onPin && (
                <button type="button" disabled={busy} onClick={() => onPin(index)}>
                  Pin this release
                </button>
              )}
              {pairs.length > 0 ? (
                index === 0 || sameAssignments ? (
                  <p className={styles.muted}>
                    {index === 0
                      ? 'Track assignments shown above.'
                      : 'Same assignments as candidate 1.'}
                  </p>
                ) : (
                  <details className={styles.candidateDetails}>
                    <summary>Review different assignments</summary>
                    <TrackGroups pairs={pairs} />
                  </details>
                )
              ) : (
                candidates.length > 0 && (
                  <p className={styles.muted}>
                    {Array.isArray(candidate.pairs)
                      ? 'No local files could be assigned to this release.'
                      : 'Track comparisons were not saved for this finding. Rerun the scan to add them.'}
                  </p>
                )
              )}
              {missing.length > 0 && (
                <details className={styles.issueDetails}>
                  <summary>
                    {missing.length} missing track{missing.length === 1 ? '' : 's'}
                  </summary>
                  <ul>
                    {missing.map((track) => (
                      <li key={track}>{track}</li>
                    ))}
                  </ul>
                </details>
              )}
              {extra.length > 0 && (
                <details className={styles.issueDetails}>
                  <summary>
                    {extra.length} unassigned file{extra.length === 1 ? '' : 's'}
                  </summary>
                  <ul>
                    {extra.map((file) => (
                      <li key={file}>{file}</li>
                    ))}
                  </ul>
                </details>
              )}
            </article>
          );
        })}
      </div>
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

function Facts({ finding, details }: { finding: ReviewFinding; details: Data }) {
  const tags = object(details.current_tags);
  const currentTitle = label(tags.title || details.current_title);
  const expectedTitle = label(details.expected_title);
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
          {folder && (
            <div>
              <dt>Folder</dt>
              <dd>{folder}</dd>
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
        <h5>Expected release</h5>
        <dl className={styles.factList}>
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
          {Boolean(details.artist_name) && (
            <div>
              <dt>Artist</dt>
              <dd>{label(details.artist_name)}</dd>
            </div>
          )}
          {expectedTitle && (
            <div>
              <dt>Track</dt>
              <dd>{expectedTitle}</dd>
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
              <dd>{label(details.release_id)}</dd>
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

export function ReconciliationEvidence({
  finding,
  onPin,
  onKeepCopy,
  busy = false,
}: {
  finding: ReviewFinding;
  onPin?: (index: number) => void;
  onKeepCopy?: (trackId: string) => void;
  busy?: boolean;
}) {
  const details = object(finding.details);
  const changes = object(details.changes);
  const missing = objects(details.missing_tracks);
  const reasons = Array.isArray(details.match_reasons) ? details.match_reasons.map(label) : [];
  const primaryPairs = objects(objects(details.candidates)[0]?.pairs);
  const duplicateCopies = objects(details.duplicate_copies);

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
            {Object.entries(changes).map(([name, raw]) => {
              const change = object(raw);
              return (
                <div key={name}>
                  <strong>{name.replaceAll('_', ' ')}</strong>
                  <span className={styles.oldValue}>{label(change.old) || '—'}</span>
                  <EvidenceIcon name="arrow" />
                  <span className={styles.newValue}>{label(change.new) || '—'}</span>
                </div>
              );
            })}
          </div>
        </section>
      )}
      <ReleaseCandidates details={details} onPin={onPin} busy={busy} />
      <DuplicateCopies details={details} onKeepCopy={onKeepCopy} busy={busy} />
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
      <details className={styles.technicalEvidence}>
        <summary>
          <EvidenceIcon name="info" /> Technical evidence
          <span>Files, tags, IDs, and scoring details</span>
        </summary>
        <div className={styles.technicalGrid}>
          <Facts finding={finding} details={details} />
          <AudioEvidence details={details} />
          <CheckedFiles details={details} />
          {reasons.length > 0 && (
            <section className={styles.technicalCard}>
              <h5>Why this track was paired</h5>
              <p>{reasons.join(' · ')}</p>
              {Boolean(details.confidence) && <p>Confidence: {label(details.confidence)}</p>}
            </section>
          )}
        </div>
      </details>
      {finding.last_error && <p className={styles.error}>Last attempt: {finding.last_error}</p>}
    </div>
  );
}
