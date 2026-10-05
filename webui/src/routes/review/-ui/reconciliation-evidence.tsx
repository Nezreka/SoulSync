import type { ReviewFinding } from '../-review.api';

import styles from './review-page.module.css';

type Data = Record<string, unknown>;

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

function audioSummary(recording: Data): string {
  return [label(recording.title), label(recording.artist), percent(recording.score)]
    .filter(Boolean)
    .join(' · ');
}

function AudioEvidence({ details }: { details: Data }) {
  const audio = object(details.acoustid);
  const recordings = objects(audio.recordings).sort((a, b) => count(b.score) - count(a.score));
  const status = label(audio.status || details.acoustid_status);
  if (!status) return null;
  return (
    <section className={styles.evidenceCard}>
      <h4>Audio identity</h4>
      <p>
        {status === 'ok'
          ? `AcoustID found ${recordings.length} recording candidate${recordings.length === 1 ? '' : 's'}.`
          : status === 'no_match'
            ? 'The fingerprint had no AcoustID match.'
            : `AcoustID status: ${status.replaceAll('_', ' ')}`}
      </p>
      {recordings.length > 0 && (
        <ol className={styles.audioCandidates}>
          {recordings.slice(0, 5).map((recording, index) => (
            <li key={`${label(recording.mbid)}-${index}`}>
              <strong>{audioSummary(recording) || 'Unnamed recording'}</strong>
              {Boolean(recording.mbid) && <small>Recording ID: {label(recording.mbid)}</small>}
            </li>
          ))}
        </ol>
      )}
      {recordings.length > 5 && <small>{recordings.length - 5} more candidates</small>}
    </section>
  );
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
  const assignments = (candidate: Data) =>
    objects(candidate.pairs)
      .map(
        (pair) =>
          `${label(pair.file)}:${label(pair.disc_number)}:${label(pair.slot_number)}:${label(pair.slot_title)}`,
      )
      .join('|');
  const sameAssignments =
    comparable &&
    candidates.every((candidate) => assignments(candidate) === assignments(candidates[0]));
  return (
    <section className={styles.evidenceCard}>
      <h4>{candidates.length ? 'Compared releases' : 'Release lookups attempted'}</h4>
      {candidates.length > 1 && (
        <p>
          {sameAssignments
            ? 'The saved file-to-track assignments are the same for these releases. The audio alone cannot identify the edition.'
            : 'These releases scored equally. Compare their assigned tracks and missing slots before choosing one.'}
        </p>
      )}
      {(candidates.length ? candidates : attempted).map((candidate, index) => {
        const pairs = objects(candidate.pairs);
        const missing = Array.isArray(candidate.missing) ? candidate.missing.map(label) : [];
        const extra = Array.isArray(candidate.extra) ? candidate.extra.map(label) : [];
        return (
          <div
            className={styles.releaseCandidate}
            key={`${label(candidate.source)}-${label(candidate.release_id)}-${index}`}
          >
            <div className={styles.releaseHeading}>
              <div>
                <strong>
                  Candidate {index + 1} · {label(candidate.source) || 'Unknown source'}
                </strong>
                <small>{label(candidate.release_id)}</small>
              </div>
              {onPin && (
                <button type="button" disabled={busy} onClick={() => onPin(index)}>
                  Pin this release
                </button>
              )}
            </div>
            {candidate.matched !== undefined && (
              <p>
                {count(candidate.matched)} files assigned · {count(candidate.clear)} clear matches
                {candidate.total_slots !== undefined &&
                  ` · ${count(candidate.total_slots)} release tracks`}
                {candidate.missing !== undefined && ` · ${missing.length} missing`}
                {candidate.extra !== undefined && ` · ${extra.length} unassigned`}
              </p>
            )}
            {pairs.length > 0 ? (
              <div className={styles.comparisonScroll}>
                <table className={styles.comparisonTable}>
                  <thead>
                    <tr>
                      <th>Local file</th>
                      <th>AcoustID identified</th>
                      <th>Release track</th>
                      <th>Why assigned</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pairs.map((pair, pairIndex) => (
                      <tr key={`${label(pair.file)}-${pairIndex}`}>
                        <td>
                          <strong>{label(pair.file)}</strong>
                          {Boolean(pair.tag_title) && <small>Tag: {label(pair.tag_title)}</small>}
                        </td>
                        <td>
                          {audioSummary({
                            title: pair.audio_title,
                            artist: pair.audio_artist,
                            score: pair.audio_score,
                          }) || 'No confident ID'}
                        </td>
                        <td>
                          {pair.slot_number
                            ? `${label(pair.disc_number || 1)}.${label(pair.slot_number)} `
                            : ''}
                          {label(pair.slot_title)}
                        </td>
                        <td>
                          <strong>{label(pair.confidence) || 'Uncertain'}</strong>
                          <small>
                            {Array.isArray(pair.reasons) ? pair.reasons.map(label).join('; ') : ''}
                          </small>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
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
              <p>
                <strong>Missing from folder:</strong> {missing.join(', ')}
              </p>
            )}
            {extra.length > 0 && (
              <p>
                <strong>Unassigned files:</strong> {extra.join(', ')}
              </p>
            )}
          </div>
        );
      })}
    </section>
  );
}

function CheckedFiles({ details }: { details: Data }) {
  const files = objects(details.checked_files);
  if (!files.length) return null;
  return (
    <section className={styles.evidenceCard}>
      <h4>Files checked</h4>
      <div className={styles.comparisonScroll}>
        <table className={styles.comparisonTable}>
          <thead>
            <tr>
              <th>File</th>
              <th>Embedded title</th>
              <th>AcoustID result</th>
            </tr>
          </thead>
          <tbody>
            {files.map((file, index) => (
              <tr key={`${label(file.file)}-${index}`}>
                <td>{label(file.file)}</td>
                <td>{label(file.tag_title) || '—'}</td>
                <td>
                  {audioSummary({
                    title: file.audio_title,
                    artist: file.audio_artist,
                    score: file.audio_score,
                  }) ||
                    label(file.audio_status) ||
                    'No match'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function ReconciliationEvidence({
  finding,
  onPin,
  busy = false,
}: {
  finding: ReviewFinding;
  onPin?: (index: number) => void;
  busy?: boolean;
}) {
  const details = object(finding.details);
  const tags = object(details.current_tags);
  const changes = object(details.changes);
  const missing = objects(details.missing_tracks);
  const reasons = Array.isArray(details.match_reasons) ? details.match_reasons.map(label) : [];
  const currentTitle = label(tags.title || details.current_title);
  const expectedTitle = label(details.expected_title);
  const folder = label(details.album_folder);

  return (
    <div className={styles.reconciliationEvidence}>
      <div className={styles.evidenceColumns}>
        <section className={styles.evidenceCard}>
          <h4>Local evidence</h4>
          <dl className={styles.factList}>
            {finding.file_path && (
              <div>
                <dt>File</dt>
                <dd>{finding.file_path.split('/').pop()}</dd>
              </div>
            )}
            {currentTitle && (
              <div>
                <dt>Embedded title</dt>
                <dd>{currentTitle}</dd>
              </div>
            )}
            {Boolean(tags.artist) && (
              <div>
                <dt>Embedded artist</dt>
                <dd>{label(tags.artist)}</dd>
              </div>
            )}
            {Boolean(tags.album) && (
              <div>
                <dt>Embedded album</dt>
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
                <dt>Audio files checked</dt>
                <dd>{label(details.file_count)}</dd>
              </div>
            )}
          </dl>
        </section>
        <section className={styles.evidenceCard}>
          <h4>Expected release</h4>
          <dl className={styles.factList}>
            {Boolean(details.album_title) && (
              <div>
                <dt>Album</dt>
                <dd>{label(details.album_title)}</dd>
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
            {details.local_file_count !== undefined &&
              details.release_track_count !== undefined && (
                <div>
                  <dt>Coverage</dt>
                  <dd>
                    {label(details.local_file_count)} files / {label(details.release_track_count)}{' '}
                    release tracks
                  </dd>
                </div>
              )}
          </dl>
        </section>
      </div>
      <AudioEvidence details={details} />
      {reasons.length > 0 && (
        <section className={styles.evidenceCard}>
          <h4>Why this track was paired</h4>
          <p>{reasons.join(' · ')}</p>
          {Boolean(details.confidence) && <p>Confidence: {label(details.confidence)}</p>}
        </section>
      )}
      {Object.keys(changes).length > 0 && (
        <section className={styles.evidenceCard}>
          <h4>Proposed changes</h4>
          <ul className={styles.changeList}>
            {Object.entries(changes).map(([name, raw]) => {
              const change = object(raw);
              return (
                <li key={name}>
                  <strong>{name.replaceAll('_', ' ')}</strong>: {label(change.old) || '—'} →{' '}
                  {label(change.new) || '—'}
                </li>
              );
            })}
          </ul>
        </section>
      )}
      <ReleaseCandidates details={details} onPin={onPin} busy={busy} />
      <CheckedFiles details={details} />
      {missing.length > 0 && (
        <section className={styles.evidenceCard}>
          <h4>Tracks absent from this folder</h4>
          <ol>
            {missing.map((track, index) => (
              <li key={`${label(track.title || track.name)}-${index}`}>
                {label(track.title || track.name)}
              </li>
            ))}
          </ol>
        </section>
      )}
      {Array.isArray(details.competing_copies) && details.competing_copies.length > 0 && (
        <section className={styles.evidenceCard}>
          <h4>Other files that fit this slot</h4>
          <ul>
            {details.competing_copies.map((path, index) => (
              <li key={index}>{label(path)}</li>
            ))}
          </ul>
        </section>
      )}
      {finding.last_error && <p className={styles.error}>Last attempt: {finding.last_error}</p>}
    </div>
  );
}
