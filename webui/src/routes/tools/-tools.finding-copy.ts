/**
 * What a finding says, in the words of the person reading it.
 *
 * A finding is stored the way its job wrote it: a title like "Metadata
 * issues" or "Missing lyrics: Brown Eyed Girl", a description like "genre:
 * mismatch" or '"X" by Y has no .lrc — lyrics found on LRClib.', and a bag of
 * details. Rows built from those repeat the type on every line, quote the
 * title back at you and lean on file extensions and field keys.
 *
 * This turns one finding into the four things a row has to say, the same way
 * for every type:
 *
 *   title    what it is about: the track, the album, the file
 *   sub      whose it is: artist and album, or the folder
 *   problem  what is wrong, as one plain sentence
 *   fix      what the fix button will do, when that is known for certain
 *
 * A type with no entry here keeps its stored title and description, so a new
 * job's findings still read as their job wrote them. Nothing here is guessed:
 * a `fix` line is only written for a fix whose behaviour is fixed in the
 * backend, and it names the button by the verb the catalog serves.
 *
 * Pure, so every line of copy is pinned by tests.
 */

type Details = Record<string, unknown>;

export interface FindingCopy {
  title: string;
  sub: string;
  problem: string;
  fix: string;
}

export interface FindingLike {
  finding_type: string;
  title: string;
  description?: string | null;
  file_path?: string | null;
  details?: Details | null;
  /** an open re-tag finding: each tag it would change, old value and new */
  preview?: unknown;
}

const str = (value: unknown): string =>
  typeof value === 'string' ? value.trim() : typeof value === 'number' ? String(value) : '';

const first = (...values: unknown[]): string => {
  for (const value of values) {
    const text = str(value);
    if (text) return text;
  }
  return '';
};

const obj = (value: unknown): Details =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Details) : {};

const arr = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

function baseName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || '';
}

function parentName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 2] : '';
}

/** "Genre", "Genre and Year", "Genre, Year and Cover". */
export function listWords(words: readonly string[]): string {
  if (words.length <= 1) return words[0] || '';
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
}

/** the words for a tag check, as the tag is called in a player */
export const TAG_CHECK_LABELS: Record<string, string> = {
  title: 'Title',
  artist: 'Artist',
  album: 'Album',
  albumartist: 'Album artist',
  track_number: 'Track number',
  disc_number: 'Disc number',
  total_tracks: 'Track total',
  total_discs: 'Disc total',
  year: 'Year',
  genre: 'Genre',
  cover: 'Cover',
  artwork_database: 'Library cover',
  artwork_sidecar: 'Folder cover',
  edition: 'Edition',
};

export function tagCheckLabel(key: string): string {
  return TAG_CHECK_LABELS[key] || key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

/** one tag a re-tag changes: what the file holds and what replaces it */
export interface TagChange {
  key: string;
  label: string;
  before: string;
  after: string;
  /** a value a person set by hand is what gets written */
  manual?: boolean;
}

/** what one fix changed in one file, as the server keeps it on the finding */
export interface AppliedFile {
  path: string;
  fields: TagChange[];
  cover: 'added' | 'replaced' | null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function readTagChanges(value: unknown): TagChange[] {
  if (!Array.isArray(value)) return [];
  const rows: TagChange[] = [];
  for (const raw of value) {
    const row = record(raw);
    if (!row || typeof row.key !== 'string') continue;
    rows.push({
      key: row.key,
      label: typeof row.label === 'string' ? row.label : row.key,
      before: str(row.before),
      after: str(row.after),
      manual: row.manual === true,
    });
  }
  return rows;
}

/** `details.applied`: every file a fix wrote, and when */
export function readApplied(details: unknown): { at: string; files: AppliedFile[] } | null {
  const applied = record(record(details)?.applied);
  if (!applied || !Array.isArray(applied.changes)) return null;
  const files: AppliedFile[] = [];
  for (const raw of applied.changes) {
    const change = record(raw);
    if (!change) continue;
    const fields = readTagChanges(change.fields);
    const cover = change.cover === 'added' || change.cover === 'replaced' ? change.cover : null;
    if (!fields.length && !cover) continue;
    files.push({ path: typeof change.path === 'string' ? change.path : '', fields, cover });
  }
  return files.length ? { at: typeof applied.at === 'string' ? applied.at : '', files } : null;
}

/** `details.undone`: the note left when a fix was taken back */
export function readUndone(
  details: unknown,
): { at: string; kept: string[]; coverLeft: boolean } | null {
  const undone = record(record(details)?.undone);
  if (!undone) return null;
  return {
    at: typeof undone.at === 'string' ? undone.at : '',
    kept: Array.isArray(undone.kept) ? undone.kept.map(str).filter(Boolean) : [],
    coverLeft: undone.cover_left === true,
  };
}

const KEY_ALIASES: Record<string, string> = {
  album_artist: 'albumartist',
  cover_art: 'cover',
  cover_sidecar: 'artwork_sidecar',
};

/** the tag's name as a player shows it, whatever the writer calls it */
export function tagChangeLabel(change: Pick<TagChange, 'key' | 'label'>): string {
  const key = KEY_ALIASES[change.key] || change.key;
  // the tags a player shows share one vocabulary; anything else (a source
  // id, a file's location) comes with its own label
  return TAG_CHECK_LABELS[key] || change.label || tagCheckLabel(key);
}

export function tagChangeBefore(change: TagChange): string {
  if (change.key === 'cover_art')
    return change.before ? 'A cover is embedded' : 'No cover embedded';
  if (change.key === 'cover_sidecar') return 'No cover.jpg in the folder';
  return change.before;
}

export function tagChangeAfter(change: TagChange): string {
  if (change.key === 'cover_art') return "The library's cover";
  if (change.key === 'cover_sidecar') return 'cover.jpg saved beside the tracks';
  return change.after;
}

const PROVIDER_NAMES: Record<string, string> = {
  caa: 'Cover Art Archive',
  musicbrainz: 'MusicBrainz',
  discogs: 'Discogs',
  deezer: 'Deezer',
  spotify: 'Spotify',
  itunes: 'Apple Music',
  lastfm: 'Last.fm',
  qobuz: 'Qobuz',
  tidal: 'Tidal',
  audiodb: 'TheAudioDB',
};

const provider = (id: unknown): string => {
  const key = str(id).toLowerCase();
  return PROVIDER_NAMES[key] || str(id);
};

/** the stored title without the type it repeats: "Missing lyrics: X" -> "X" */
function withoutPrefix(title: string): string {
  const at = title.indexOf(': ');
  return at > 0 && at < 32 ? title.slice(at + 2).trim() : title;
}

interface Subject {
  track: string;
  artist: string;
  album: string;
  file: string;
  folder: string;
}

function subjectOf(finding: FindingLike, d: Details): Subject {
  const reference = obj(obj(d.validation).reference);
  const path = first(finding.file_path, d.file_path, d.original_path);
  return {
    track: first(
      d.track_title,
      d.title,
      d.expected_title,
      d.matched_title,
      d.file_title,
      reference.title,
    ),
    artist: first(
      d.artist,
      d.artist_name,
      d.expected_artist,
      reference.track_artist,
      reference.artist_name,
    ),
    album: first(d.album_title, d.album, reference.album_title),
    file: baseName(path),
    folder: first(d.folder) ? baseName(str(d.folder)) : parentName(path),
  };
}

const joinSub = (...parts: string[]): string => parts.filter(Boolean).join(' · ');

type Writer = (finding: FindingLike, d: Details, s: Subject, verb: string) => Partial<FindingCopy>;

/**
 * what the button on a bad file does. with a track behind the file it opens
 * the source picker; a file the library does not know can only be deleted,
 * and the row's button says so ("Delete File"). any other verb is left
 * unexplained rather than guessed at.
 */
const redownload = (verb: string) =>
  /delete/i.test(verb)
    ? `${verb} removes this file. It asks first.`
    : /download|upgrade/i.test(verb)
      ? `${verb} lets you pick where to get a fresh copy from.`
      : '';

const WRITERS: Record<string, Writer> = {
  library_retag: (finding, d, s, verb) => {
    const checks = obj(obj(d.validation).checks);
    const wrong: string[] = [];
    const missing: string[] = [];
    for (const [key, value] of Object.entries(checks)) {
      if (value === 'mismatch') wrong.push(tagCheckLabel(key));
      else if (value === 'missing') missing.push(tagCheckLabel(key));
    }
    const lower = (words: string[]) =>
      words.map((word, index) => (index === 0 ? word : word.toLowerCase()));
    const sentences = [];
    if (wrong.length) {
      sentences.push(
        `${listWords(lower(wrong))} in the file ${wrong.length === 1 ? 'does' : 'do'} not match the library.`,
      );
    }
    if (missing.length) {
      sentences.push(
        `${listWords(lower(missing))} ${missing.length === 1 ? 'is' : 'are'} missing from the file.`,
      );
    }
    if (!s.track || !sentences.length) return {};
    // with the values to hand, the row says the change itself: "Year 2024 to
    // 2025" beats "year does not match" when there are few enough to read
    const changes = readTagChanges(finding.preview);
    const short = (value: string) => (value.length > 36 ? `${value.slice(0, 35)}…` : value);
    const spelled =
      changes.length > 0 && changes.length <= 3
        ? changes
            .map((change) => {
              const label = tagChangeLabel(change);
              if (change.key === 'cover_art' || change.key === 'cover_sidecar') {
                return `${label}: ${tagChangeAfter(change).toLowerCase()}`;
              }
              return change.before
                ? `${label}: ${short(change.before)} → ${short(change.after)}`
                : `${label}: empty → ${short(change.after)}`;
            })
            .join('  ·  ')
        : '';
    return {
      title: s.track,
      sub: joinSub(s.artist, s.album),
      problem: spelled || sentences.join(' '),
      fix: `${verb} writes the library's ${listWords([...wrong, ...missing].map((word) => word.toLowerCase()))} into the file. Nothing else in it changes.`,
    };
  },

  missing_lyrics: (finding, _d, s, verb) => ({
    title: s.track || withoutPrefix(finding.title),
    sub: joinSub(s.artist, s.album),
    problem: /found on lrclib/i.test(finding.description || '')
      ? 'No lyrics saved for this track. LRClib has them.'
      : 'No lyrics saved for this track.',
    fix: `${verb} saves the lyrics beside the track and inside its tags.`,
  }),

  missing_cover_art: (finding, d, s, verb) => {
    const places = [
      d.db_missing === true ? 'the library' : '',
      d.embed_missing === true ? 'the audio files' : '',
      d.sidecar_missing === true ? 'the album folder' : '',
    ].filter(Boolean);
    const source = provider(d.artwork_source);
    return {
      title: s.album || withoutPrefix(finding.title),
      sub: s.artist,
      problem: `${places.length ? `No cover in ${listWords(places)}.` : 'This album is missing artwork.'}${
        d.found_artwork_url ? ` ${source || 'A source'} has one.` : ''
      }`,
      fix: `${verb} puts the found cover where it is missing. A cover you already have is kept.`,
    };
  },

  canonical_version: (finding, d, s, verb) => {
    const best = obj(arr(d.candidates)[0]);
    const score = Number(best.score);
    const owned = Number(d.file_track_count);
    const listed = Number(best.track_count);
    const fit =
      Number.isFinite(score) && score > 0
        ? ` The closest is a ${provider(best.source)} release at ${Math.round(score * 100)}%${
            owned > 0 && listed > 0
              ? ` (you have ${owned} track${owned === 1 ? '' : 's'}, it lists ${listed})`
              : ''
          }.`
        : '';
    return {
      title: s.album || withoutPrefix(finding.title),
      sub: s.artist,
      problem: `Several editions match this album and none is chosen.${fit}`,
      fix: `${verb} records that edition as the one you have. No file is touched.`,
    };
  },

  orphan_file: (finding, d, s, verb) => {
    const format = str(d.format).toUpperCase();
    const bitrate = Number(d.bitrate);
    const quality = [format, bitrate > 0 ? `${bitrate} kbps` : ''].filter(Boolean).join(' ');
    return {
      title: s.file || withoutPrefix(finding.title),
      sub: s.folder,
      problem: `This audio file is on disk but not in your library.${quality ? ` It is ${quality}.` : ''}`,
      fix: `${verb} asks whether to send it to staging to be imported, or delete it.`,
    };
  },

  dead_file: (finding, _d, s, verb) => ({
    title: s.track || withoutPrefix(finding.title),
    sub: joinSub(s.artist, s.album),
    problem: 'The library lists this track, but its file is gone.',
    fix: [redownload(verb), 'Remove takes the track out of your library and downloads nothing.']
      .filter(Boolean)
      .join(' '),
  }),

  library_unreachable: (finding, d, _s, verb) => {
    const outage = obj(d.outage);
    const count = Number(outage.unreachable) || 0;
    const checked = Number(outage.checked) || 0;
    return {
      title: count
        ? `${count.toLocaleString()} of ${checked.toLocaleString()} files can't be reached`
        : withoutPrefix(finding.title),
      sub: str(outage.example_path),
      problem:
        'This many files missing at once is usually a disconnected drive or a path mapping, so nothing was marked missing.',
      fix: `If you deleted them yourself, ${verb} marks them missing and lists each one under Dead Files.`,
    };
  },

  untagged_download: (finding, d, s, verb) => {
    const missing = arr(d.missing).map(str).filter(Boolean);
    return {
      title: s.track || s.file || withoutPrefix(finding.title),
      sub: joinSub(s.artist, s.album),
      problem: missing.length
        ? `This download has no ${listWords(missing)} tag.`
        : 'This download arrived with tags missing.',
      fix: `${verb} fills them in from what SoulSync knows about the track.`,
    };
  },

  native_duplicate_tracks: (finding, _d, s, verb) => ({
    title: s.track || withoutPrefix(finding.title),
    sub: joinSub(s.artist, s.album),
    problem: 'More than one file holds this recording.',
    fix: `${verb} keeps the best copy and moves the others to quarantine, where they can be restored.`,
  }),

  native_duplicate_releases: (finding, _d, s, verb) => ({
    title: s.album || withoutPrefix(finding.title),
    sub: s.artist,
    problem: 'Two entries in your library look like the same release.',
    fix: `${verb} joins them under one release. Every file and edition is kept.`,
  }),

  duplicate_tracks: (finding, _d, s) => ({
    title: s.track || withoutPrefix(finding.title),
    sub: joinSub(s.artist, s.album),
    problem:
      'More than one copy of this song was found before the library upgrade. Run the Duplicate Detector to review them with today’s checks.',
  }),

  fake_lossless: (finding, d, s, verb) => {
    const cutoff = Number(d.cutoff_khz ?? d.detected_cutoff_khz ?? d.cutoff);
    const format = str(d.format).toUpperCase() || 'lossless file';
    return {
      title: s.track || s.file || withoutPrefix(finding.title),
      sub: joinSub(s.artist, s.album),
      problem: `This ${format} looks like it was made from a lossy file${
        Number.isFinite(cutoff) && cutoff > 0
          ? `: its audio stops at ${cutoff} kHz.`
          : ', going by where its audio stops.'
      }`,
      fix: redownload(verb),
    };
  },

  corrupt_audio: (finding, _d, s, verb) => ({
    title: s.track || s.file || withoutPrefix(finding.title),
    sub: joinSub(s.artist, s.album),
    problem: 'This file does not play through. It is cut off or damaged.',
    fix: redownload(verb),
  }),

  short_preview_track: (finding, d, s, verb) => {
    const has = d.file_duration_s != null && d.expected_duration_s != null;
    return {
      title: s.track || withoutPrefix(finding.title),
      sub: joinSub(s.artist, s.album),
      problem: has
        ? `This file is ${str(d.file_duration_s)} seconds long. The real track runs ${str(d.expected_duration_s)} seconds, so this is a preview clip.`
        : 'This file is a short preview clip, not the full track.',
      fix: redownload(verb),
    };
  },

  quality_upgrade: (finding, d, s, verb) => {
    const quality = first(d.current_quality, d.current_format);
    return {
      title: s.track || withoutPrefix(finding.title),
      sub: joinSub(s.artist, s.album),
      problem: `This track is below your quality profile${quality ? `: it is ${quality}.` : '.'}`,
      fix: redownload(verb),
    };
  },

  acoustid_mismatch: (finding, d, s) => {
    const heard = [str(d.acoustid_title), str(d.acoustid_artist)].filter(Boolean);
    return {
      title: s.track || withoutPrefix(finding.title),
      sub: joinSub(s.artist, s.album),
      problem: heard.length
        ? `The audio itself matches “${heard[0]}”${heard[1] ? ` by ${heard[1]}` : ''}, not what the file is tagged as.`
        : 'The audio does not match what the file is tagged as.',
    };
  },

  acoustid_no_match: (finding, _d, s) => ({
    title: s.track || withoutPrefix(finding.title),
    sub: joinSub(s.artist, s.album),
    problem: 'No fingerprint match was found for this audio, so it could not be confirmed.',
  }),

  track_number_mismatch: (finding, d, s, verb) => {
    const has = d.current_track_num !== undefined && d.correct_track_num !== undefined;
    return {
      title: first(d.file_title, d.matched_title) || s.track || withoutPrefix(finding.title),
      sub: joinSub(s.artist, s.album),
      problem: has
        ? `Tagged as track ${str(d.current_track_num)}. The release lists it as track ${str(d.correct_track_num)}.`
        : 'The track number does not match the release.',
      fix: `${verb} writes the release's track number into the file's tags and renames the file to match.`,
    };
  },

  comma_artist_split: (finding, d, _s, verb) => {
    const parts = arr(d.split_artists).map(str).filter(Boolean);
    const next = first(d.new_display_artist) || parts.join('; ');
    const count = Number(d.track_count);
    return {
      title: first(d.combined_name) || withoutPrefix(finding.title),
      sub: count > 0 ? `${count} track${count === 1 ? '' : 's'}` : '',
      problem: `This artist is really ${parts.length || 'several'} artists in one name.`,
      fix: next ? `${verb} changes the artist tag to “${next}”.` : '',
    };
  },

  genre_cleanup: (finding, d, s, verb) => {
    const removed = arr(d.removed_genres).map(str).filter(Boolean);
    const kept = arr(d.kept_genres).map(str).filter(Boolean);
    return {
      title: s.album || s.artist || withoutPrefix(finding.title),
      sub: s.album ? s.artist : '',
      problem: removed.length
        ? `${listWords(removed)} ${removed.length === 1 ? 'is' : 'are'} not on your genre list.`
        : 'Some of these genres are not on your genre list.',
      fix: `${verb} ${kept.length ? `keeps ${listWords(kept)} and removes the rest` : 'removes them'}.`,
    };
  },

  missing_replaygain: (finding, _d, s, verb) => ({
    title: s.track || withoutPrefix(finding.title),
    sub: joinSub(s.artist, s.album),
    problem: 'This track has no loudness tag, so it may play louder or quieter than the rest.',
    fix: `${verb} measures the track and writes the tag.`,
  }),

  zero_length_mp4: (finding, _d, s, verb) => ({
    title: s.track || s.file || withoutPrefix(finding.title),
    sub: joinSub(s.artist, s.album),
    problem: 'This file shows 0:00 and cannot be skipped through.',
    fix: `${verb} repairs the file in place. Nothing is downloaded again.`,
  }),

  empty_folder: (finding, d, _s, verb) => {
    const path = first(d.folder_path, finding.file_path);
    const junk = arr(d.junk_files).length;
    return {
      title: baseName(path) || withoutPrefix(finding.title),
      sub: parentName(path),
      problem: junk
        ? `This folder holds no music, only ${junk} leftover file${junk === 1 ? '' : 's'}.`
        : 'This folder is empty.',
      fix: `${verb} removes the folder.`,
    };
  },

  path_mismatch: (finding, _d, s, verb) => ({
    title: s.track || s.file || withoutPrefix(finding.title),
    sub: joinSub(s.artist, s.album),
    problem: 'This file is not where your folder template says it belongs.',
    fix: `${verb} moves it to the expected place.`,
  }),

  suspect_album_tag: (finding, d, s, verb) => {
    const reasons = arr(d.reasons).map(str).filter(Boolean);
    return {
      title: s.track || s.album || withoutPrefix(finding.title),
      sub: joinSub(s.artist, s.album),
      problem: `This is probably filed under the wrong album.${reasons.length ? ` ${reasons.join('. ')}.` : ''}`,
      fix: `${verb} lets you pick the right release.`,
    };
  },

  incomplete_album: (finding, d, s) => {
    const have = Number(d.actual_tracks ?? d.owned_tracks);
    const want = Number(d.expected_tracks ?? d.total_tracks);
    return {
      title: s.album || withoutPrefix(finding.title),
      sub: s.artist,
      problem:
        have >= 0 && want > 0
          ? `You have ${have} of ${want} tracks.`
          : 'Some tracks of this album are missing.',
    };
  },
};

/**
 * The title, second line, problem and fix sentence of one finding.
 *
 * `verb` is the fix button's label from the served catalog ("Apply Tags"). A
 * finding with no fix gets no `fix` line.
 */
export function describeFinding(finding: FindingLike, verb?: string | null): FindingCopy {
  const d = obj(finding.details);
  const subject = subjectOf(finding, d);
  const stored: FindingCopy = {
    title: finding.title,
    sub: joinSub(subject.artist, subject.album),
    problem: finding.description || '',
    fix: '',
  };
  const writer = WRITERS[finding.finding_type];
  if (!writer) return stored;
  const written = writer(finding, d, subject, verb || 'The fix');
  const copy = {
    title: written.title || stored.title,
    sub: written.sub ?? stored.sub,
    problem: written.problem || stored.problem,
    fix: verb ? written.fix || '' : '',
  };
  // a second line that only repeats the title says nothing
  if (copy.sub === copy.title) copy.sub = '';
  return copy;
}
