/**
 * The video organization settings (video-organization.ts), drawn where DESIGN
 * 3.3 puts each one (settings-bic phase 4):
 *   Library > Naming, Video: the movie, episode and YouTube templates with an
 *     example under each, and colons in titles;
 *   Library > Import and files, Video: when a file finishes, ffprobe, replace,
 *     the recycle bin;
 *   Library > Tags and artwork, Video: subtitles carried along, artwork, NFO;
 *   Library > Subtitles: the whole subtitles group, the provider chain on the
 *     shared order list;
 *   Quality > YouTube: SponsorBlock and embedded subtitles (YoutubeFileOptions);
 *   Downloads > Behaviour, Video: the YouTube follow count (YoutubeFollowCount).
 * The minimum free disk space is drawn beside the download folder on Folders.
 *
 * It is one object on the server, saved whole: a switch or a pick saves at
 * once, a text field when it is left, as before. Each block listens for its
 * own fields being left (VoBlock).
 */

import { type ReactNode, useEffect, useRef } from 'react';

import {
  Btn,
  Card,
  Mark,
  More,
  NumberField,
  OrderList,
  Row,
  SectionLabel,
  Segmented,
  Select,
  StatusWords,
  Switch,
  TemplateField,
  TextField,
} from '../ui';
import s from '../ui/settings.module.css';
import { shell, useBusyGate } from './library-card';
import {
  ANIME_EG,
  EPISODE_EG,
  MISSING_SUBS_JOB,
  MOVIE_EG,
  SUBCHAIN_NETWORK_IDS,
  YOUTUBE_EG,
  jobLine,
  renderTemplate,
  subchainMeta,
  useVideoOrganization,
  videoOrganization,
  whisperBytes,
} from './video-organization';

/** the text fields that save when left (the browser's change event) */
const SAVED_WHEN_LEFT: Record<string, true> = {
  'vo-movie-template': true,
  'vo-episode-template': true,
  'vo-anime-episode-template': true,
  'vo-youtube-template': true,
  'vo-recycle-days': true,
  'vo-recycle-path': true,
  'vo-min-free': true,
  'vo-yt-follow-count': true,
  'vo-sub-min-score': true,
  'vo-sub-daily-quota': true,
  'vo-sub-upgrade-delta': true,
  'vo-sub-worker-interval': true,
  'vo-sub-worker-batch': true,
};

const save = (key: string, value: string | boolean) =>
  void videoOrganization.change({ [key]: value });

const MOVIE_PH = '$title ($year)/$title ($year) $quality';
const EPISODE_PH = '$series/Season $season/$series - S$seasonE$episode - $episodetitle $quality';
const YOUTUBE_PH = '$channel/Season $year/$channel - $sxe - $title';
const ANIME_PH =
  '$series/Season $season/$series - S$seasonE$episode - $absolute - $episodetitle $quality';

/**
 * A block of video organization settings: it loads them, says when they are
 * loading or failed (with Retry), turns its controls off meanwhile, and saves
 * a text field that is left (the languages are checked first).
 */
function VoBlock({ id, children }: { id?: string; children: ReactNode }) {
  const { status } = useVideoOrganization();
  const block = useRef<HTMLDivElement | null>(null);
  const busy = status === 'loading' || status === 'failed';
  useBusyGate(block, busy);
  useEffect(() => {
    const el = block.current;
    if (!el) return;
    const left = (event: Event) => {
      const target = (event.target as Element | null)?.id || '';
      if (target === 'vo-sub-langs') videoOrganization.commitLangs();
      else if (SAVED_WHEN_LEFT[target]) void videoOrganization.save();
    };
    el.addEventListener('change', left);
    return () => el.removeEventListener('change', left);
  }, []);
  return (
    <div id={id} ref={block} aria-busy={status === 'idle' ? undefined : busy ? 'true' : 'false'}>
      {busy ? (
        <p className={s.loadNote} role="status">
          {status === 'loading' ? (
            'Loading saved video settings...'
          ) : (
            <>
              Could not load saved video settings.{' '}
              <button
                type="button"
                className="stg-library-retry"
                onClick={() => void videoOrganization.load()}
              >
                Retry
              </button>
            </>
          )}
        </p>
      ) : null}
      {children}
    </div>
  );
}

function Check({
  id,
  field,
  label,
  help,
  more,
  sub,
  disabled,
}: {
  id: string;
  field: string;
  label: ReactNode;
  help?: ReactNode;
  more?: ReactNode;
  sub?: boolean;
  disabled?: boolean;
}) {
  const { form, status } = useVideoOrganization();
  // off while the settings load, as every control of the block is
  const busy = status === 'loading' || status === 'failed';
  return (
    <Row label={label} htmlFor={id} help={help} more={more} sub={sub} off={disabled}>
      <Switch
        id={id}
        checked={!!form[field]}
        disabled={busy || disabled}
        onChange={(event) => save(field, event.target.checked)}
      />
    </Row>
  );
}

/** a field typed in: the value kept, saved when it is left (the block hears it) */
function useField(field: string) {
  const { form } = useVideoOrganization();
  return {
    value: String(form[field] ?? ''),
    onChange: (value: string) => videoOrganization.edit({ [field]: value }),
  };
}

function Num({
  id,
  field,
  min,
  max,
  step = '1',
  unit,
  placeholder,
}: {
  id: string;
  field: string;
  min: string;
  max?: string;
  step?: string;
  unit?: string;
  placeholder?: string;
}) {
  const f = useField(field);
  return (
    <NumberField
      id={id}
      min={min}
      max={max}
      step={step}
      unit={unit}
      placeholder={placeholder}
      value={f.value}
      onChange={(event) => f.onChange(event.target.value)}
    />
  );
}

// ── Naming > Video ──

function useExamples() {
  const { form, loads } = useVideoOrganization();
  const colons = String(form.colon_replacement || 'delete');
  const example = (field: string, placeholder: string, eg: Record<string, string>, ext: string) =>
    loads ? renderTemplate(String(form[field] || '') || placeholder, eg, colons) + ext : null;
  return {
    movie: example('movie_template', MOVIE_PH, MOVIE_EG, '.mkv'),
    episode: example('episode_template', EPISODE_PH, EPISODE_EG, '.mkv'),
    // an empty anime template means anime uses the episode template, so that
    // is what it shows, drawn on an anime sample
    anime: loads
      ? renderTemplate(
          String(form.anime_episode_template || '') ||
            String(form.episode_template || '') ||
            EPISODE_PH,
          ANIME_EG,
          colons,
        ) + '.mkv'
      : null,
    youtube: example('youtube_template', YOUTUBE_PH, YOUTUBE_EG, '.mp4'),
  };
}

/** "a/b/c.mkv" as the template field's folders and file */
const crumbs = (path: string | null) => {
  if (!path) return null;
  const parts = path.split('/').filter(Boolean);
  return { folders: parts.slice(0, -1), file: parts[parts.length - 1] };
};

function VideoTemplate({
  id,
  field,
  label,
  placeholder,
  example,
  help,
  more,
}: {
  id: string;
  field: string;
  label: string;
  placeholder: string;
  example: string | null;
  help: ReactNode;
  more?: ReactNode;
}) {
  const f = useField(field);
  return (
    <Row stack label={label} htmlFor={id} help={help} more={more}>
      <TemplateField
        id={id}
        value={f.value}
        onChange={f.onChange}
        placeholder={placeholder}
        preview={crumbs(example)}
      />
      {/* the example as one line too, as before */}
      <code hidden id={`${id.replace('-template', '')}-preview`}>
        {example ?? '…'}
      </code>
    </Row>
  );
}

export function VideoNaming() {
  const { form } = useVideoOrganization();
  const eg = useExamples();
  async function reset() {
    const confirm = shell().showConfirmDialog;
    if (confirm) {
      const ok = await confirm({
        title: 'Put the standard layout back?',
        message:
          'The movie, episode and YouTube templates go back to the Radarr and Sonarr standard. So do when a file finishes (copy), ffprobe, replace and carry subtitles (on), artwork, NFO and downloading subtitles (off), subtitle languages (en), the recycle bin (on, 7 days), SponsorBlock (off), minimum free space (0) and the YouTube follow count (5). Colons, the provider chain, Whisper and the advanced subtitle numbers stay.',
        confirmText: 'Put it back',
        cancelText: 'Keep mine',
      });
      if (!ok) return;
    }
    void videoOrganization.reset();
  }
  return (
    <VoBlock id="organization-video-panel">
      <SectionLabel
        title="Path templates"
        hint="Folders split on /, the last part is the file name"
        action={
          <Btn kind="ghost" small icon="undo" id="vo-reset" onClick={() => void reset()}>
            Reset to defaults
          </Btn>
        }
      />
      <Card>
        <VideoTemplate
          id="vo-movie-template"
          field="movie_template"
          label="Movie path template"
          placeholder={MOVIE_PH}
          example={eg.movie}
          help="Filled in with The Matrix (1999) as the example."
          more={
            <>
              Variables: $title, $titlefirst, $year, $quality (like Bluray-1080p), $resolution,
              $source, $codec, $edition, $audiolangs, $sublangs, $tmdbid, $imdbid.{' '}
              <code>[$audiolangs]</code> gives <code>[JA+EN]</code> and disappears when a file has
              no language tags.
            </>
          }
        />
        <VideoTemplate
          id="vo-episode-template"
          field="episode_template"
          label="Episode path template"
          placeholder={EPISODE_PH}
          example={eg.episode}
          help="Filled in with Dexter: Resurrection, its first episode, as the example."
          more="Variables: $series, $season (01), $seasonraw (1), $episode (01), $episodetitle, $year, $quality, $resolution, $source, $codec, $audiolangs, $sublangs, $tmdbid, $imdbid, $tvdbid (the show's ids), $absolute (001, the episode's number counted across every season, for anime)."
        />
        <VideoTemplate
          id="vo-anime-episode-template"
          field="anime_episode_template"
          label="Anime episode path template"
          placeholder={ANIME_PH}
          example={eg.anime}
          help="Used for anime shows (set by hand, or in a library marked Anime) instead of the episode template. Leave it empty and anime is named like any other show."
          more="Same variables as episodes. Add $absolute for the episode's number counted across every season (026 for S02E01 when season 1 has 25), the way anime releases are numbered, but keep S$seasonE$episode in it too: that's what your media server matches on."
        />
        <Row
          label="Colons in titles"
          htmlFor="vo-colon-replacement"
          help="File systems don't allow a colon, so it is replaced in movie and episode names. Mass Rename brings files you have in line."
        >
          <Select
            id="vo-colon-replacement"
            value={String(form.colon_replacement ?? '')}
            onChange={(event) => save('colon_replacement', event.target.value)}
            options={[
              { value: 'delete', label: 'Delete (Dexter Resurrection)' },
              { value: 'smart', label: 'Smart (Dexter - Resurrection, 12:30 becomes 12-30)' },
              { value: 'space_dash_space', label: 'Space dash space (Dexter - Resurrection)' },
              { value: 'dash', label: 'Dash (Dexter- Resurrection)' },
            ]}
          />
        </Row>
        <VideoTemplate
          id="vo-youtube-template"
          field="youtube_template"
          label="YouTube path template"
          placeholder={YOUTUBE_PH}
          example={eg.youtube}
          help="Followed YouTube channels are organized as a TV show."
          more={
            <>
              Variables: $channel, $year, $sxe (s2024e0315, so Plex and Jellyfin index episodes
              without an online agent), $date (2024-03-15), $month (03), $day (15), $title,
              $videoid. Plex tip: set the YouTube library&apos;s agent to{' '}
              <strong>Personal Media Shows</strong>; episode titles then come straight from the file
              name, and the channel folder gets its poster and fanart on its own.
            </>
          }
        />
      </Card>
    </VoBlock>
  );
}

export function VideoImport() {
  const { form } = useVideoOrganization();
  const recyclePath = useField('recycle_path');
  const recycle = !!form.recycle_deletes;
  return (
    <VoBlock>
      <SectionLabel title="When a file finishes" />
      <Card>
        <Row
          label="When a file finishes"
          htmlFor="vo-transfer-mode"
          help="How a finished download goes from the download folder into your library."
        >
          <Segmented
            id="vo-transfer-mode"
            label="When a file finishes"
            value={String(form.transfer_mode || 'copy')}
            options={[
              { value: 'copy', label: 'Copy in, then remove the source' },
              { value: 'move', label: 'Move into the library' },
            ]}
            onChange={(value) => save('transfer_mode', value)}
          />
        </Row>
        <Check
          id="vo-verify"
          field="verify_with_ffprobe"
          label="Verify with ffprobe"
          help="Tag by the file's real resolution and reject corrupt or sample files. Needs ffmpeg; uses the release name without it."
        />
        <Check
          id="vo-replace"
          field="replace_existing"
          label="Replace an existing copy when the new one is better"
          help="Deletes the old file when an upgrade lands."
        />
      </Card>

      <SectionLabel title="Deleting" />
      <Card>
        <Check
          id="vo-recycle"
          field="recycle_deletes"
          label="Recycle deleted files instead of erasing them"
          help="Replaced copies, cleaned-up YouTube episodes and dismissed imports go to a recycle folder instead of being deleted."
          more={
            <>
              An <code>ss_recycle</code> folder inside the library: a safety net if something is
              replaced by mistake. Copies are kept for torrents.
            </>
          }
        />
        <Row
          sub
          field
          off={!recycle}
          label="Keep recycled files for"
          htmlFor="vo-recycle-days"
          help="Older recycled files are removed for good, as new files are recycled."
        >
          <Num
            id="vo-recycle-days"
            field="recycle_keep_days"
            min="1"
            max="365"
            placeholder="7"
            unit="days"
          />
        </Row>
        <Row
          sub
          wide
          off={!recycle}
          label="Recycle folder"
          htmlFor="vo-recycle-path"
          help="Optional. One folder for everything; keep it on the same drive as your libraries so moves are instant. Empty uses an ss_recycle folder per library."
        >
          <TextField
            full
            mono
            id="vo-recycle-path"
            placeholder="empty: ss_recycle inside each library"
            value={recyclePath.value}
            onChange={(event) => recyclePath.onChange(event.target.value)}
          />
        </Row>
      </Card>
    </VoBlock>
  );
}

export function VideoTags() {
  return (
    <VoBlock>
      <Card>
        <Check
          id="vo-subs"
          field="carry_subtitles"
          label="Carry matching subtitles"
          help="Sibling .srt and .ass files come along with the video, renamed to match."
        />
        <Check
          id="vo-artwork"
          field="save_artwork"
          label="Save artwork into the folder"
          help="poster.jpg, fanart.jpg and clearlogo.png (and season posters for shows) next to the movie or in the show folder."
          more="Off by default: Plex and Jellyfin usually fetch their own once the folder is named cleanly. Turn it on to pin specific art, or for offline and local-assets setups."
        />
        <Check
          id="vo-nfo"
          field="write_nfo"
          label="Write NFO metadata files"
          help="Kodi and Jellyfin style movie.nfo and tvshow.nfo (title, plot, rating, genres, cast, ids), so the library is fully portable."
          more="Off by default. Plex ignores them; Jellyfin, Kodi and Emby use them."
        />
      </Card>
    </VoBlock>
  );
}

// ── Quality > YouTube and Downloads > Behaviour, Video ──

export function YoutubeFileOptions() {
  const { form } = useVideoOrganization();
  // Quality > YouTube: the video settings load with the pill, from either side
  useEffect(() => void videoOrganization.load(), []);
  return (
    <VoBlock>
      <SectionLabel title="What a YouTube file contains" />
      <Card>
        <Row
          label="YouTube: SponsorBlock"
          htmlFor="vo-sponsorblock"
          help="Uses the community SponsorBlock database at download time."
          more={
            <>
              <strong>Mark</strong> adds chapters you can skip (sponsor, self-promo, intro, outro);{' '}
              <strong>Cut</strong> removes sponsor, self-promo and interaction segments from the
              file.
            </>
          }
        >
          <Segmented
            id="vo-sponsorblock"
            label="YouTube: SponsorBlock"
            value={String(form.youtube_sponsorblock || 'off')}
            options={[
              { value: 'off', label: 'Off' },
              { value: 'mark', label: 'Mark as chapters' },
              { value: 'remove', label: 'Cut them out' },
            ]}
            onChange={(value) => save('youtube_sponsorblock', value)}
          />
        </Row>
        <Check
          id="vo-yt-subs"
          field="youtube_embed_subs"
          label="YouTube: embed subtitles"
          help="YouTube's subtitles in your subtitle languages (Library, Subtitles) go into the file, so Plex and Jellyfin offer them without sidecar files."
        />
      </Card>
    </VoBlock>
  );
}

export function YoutubeFollowCount() {
  // Behaviour on the music side: the video settings were never shown there
  useEffect(() => void videoOrganization.load(), []);
  return (
    <VoBlock>
      <Card title="YouTube" hint="Followed channels. Saves when the field is left.">
        <Row
          field
          label="Videos to grab when you follow a channel"
          htmlFor="vo-yt-follow-count"
          help="Following a channel grabs its most recent ones, then watches for anything new. 0 grabs only new uploads."
          more="It is also the rolling keep the last few current net the channel scan uses. It never pulls the whole back catalogue."
        >
          <Num
            id="vo-yt-follow-count"
            field="youtube_follow_count"
            min="0"
            max="100"
            placeholder="5"
            unit="videos"
          />
        </Row>
      </Card>
    </VoBlock>
  );
}

// ── Subtitles ──

/** the subtitle provider chain, on the shared order list; Whisper stays
 *  pinned last while it is on, downloaded and has its engine */
function SubtitleChain() {
  const { order, whisper, loads } = useVideoOrganization();
  const whisperOn = !!(whisper && whisper.enabled && whisper.downloaded && whisper.engine);
  const name = (id: string) => subchainMeta(id).name;
  const mark = (id: string) => (
    <Mark icon={id === 'embedded' ? 'box' : id === 'whisper' ? 'wave' : 'news'} size="sm" />
  );
  const pool = SUBCHAIN_NETWORK_IDS.filter((id) => !order.includes(id));
  // nothing is drawn until the settings load, as before
  return (
    <div id="subchain-widget">
      {loads ? (
        <OrderList
          label="Subtitle providers, tried from the top"
          items={order.map((id) => ({ id, name: name(id), mark: mark(id) }))}
          role={(_id, index) => (index === 0 ? 'Tried first' : `Fallback ${index}`)}
          dropText={(_id, index) =>
            index === 0 ? 'Drop here: tried first' : `Drop here: fallback ${index}`
          }
          afterText={(next) => `${name(next[0] ?? '')} is tried first.`}
          onChange={(next) => videoOrganization.setOrder(next)}
          pool={pool.map((id) => ({
            id,
            name: name(id),
            mark: mark(id),
            line: id === 'embedded' ? 'Needs no key' : 'Needs its API key (Connections)',
          }))}
          poolHead={
            <>
              {/* right under the order: it is tried after every one of them */}
              {whisperOn ? (
                <div className={s.pinned} data-pinned="whisper">
                  {mark('whisper')}
                  <span>
                    <b>{name('whisper')}</b>
                    <StatusWords tone="ok">Last resort, always tried last</StatusWords>
                  </span>
                </div>
              ) : null}
              <SectionLabel title="Not used" hint="Add one to try it after the others" />
            </>
          }
          poolEmpty="Every provider is in the chain."
          keepText="Keep at least one provider: subtitles need somewhere to come from"
          removeLabel="Stop using"
          menuBodyClass="stg-menu-open"
        />
      ) : null}
    </div>
  );
}

/** Whisper: the model, its download, and the switch that needs both */
function Whisper() {
  const { whisper: st, form, whisperError, status } = useVideoOrganization();
  const busy = status === 'loading' || status === 'failed';
  const picked = String(form.whisper_model || '');
  const dl = st?.download || {};
  const downloading = dl.state === 'downloading';
  // "ready": downloaded, for the model the settings name now
  const pickedReady = !!st?.downloaded && picked === st?.model;
  const usable = pickedReady && !!st?.engine;
  const pct =
    downloading && (dl.total_bytes || 0) > 0
      ? Math.round(((dl.downloaded_bytes || 0) / (dl.total_bytes || 1)) * 100)
      : 0;

  let button = 'Download model';
  let buttonOff = false;
  if (st) {
    if (downloading) {
      button = `Downloading ${dl.model || picked}… ${pct}%`;
      buttonOff = true;
    } else if (pickedReady) {
      button = `${picked} downloaded`;
      buttonOff = true;
    } else button = `Download ${picked} model`;
  }

  let statusText = '';
  if (st) {
    const bits: string[] = [];
    if (st.downloaded)
      bits.push(
        `Downloaded: ${st.model} (${whisperBytes(st.size_bytes)})${st.downloaded_at ? ` on ${String(st.downloaded_at).slice(0, 10)}` : ''}.`,
      );
    else bits.push('No model downloaded yet.');
    if (st.disk_model && st.disk_model !== picked)
      bits.push(`Note: ${st.disk_model} is on disk; downloading ${picked} replaces it.`);
    if (!st.engine)
      bits.push(
        `Transcription engine missing on the server: ${st.engine_hint || 'install faster-whisper'}.`,
      );
    statusText = bits.join(' ');
  }
  const option = (value: string) => {
    const meta = st?.models?.[value];
    return { value, label: meta ? `${meta.label}: ${meta.blurb}` : value };
  };

  return (
    <>
      <Row
        wide
        label="Local transcription (Whisper)"
        htmlFor="vo-whisper-model"
        help="The last resort when no provider has subtitles: this server transcribes the video's own audio. No key, no quota, offline after the one-time model download."
        more="It runs in the background subtitle worker (a 45-minute episode takes roughly 10 to 20 minutes on a typical CPU). It needs the faster-whisper Python package on the server. The model you pick is saved when it is downloaded."
      >
        <div className={s.whisperLine}>
          <Select
            id="vo-whisper-model"
            aria-label="Whisper model"
            value={picked}
            onChange={(event) => videoOrganization.edit({ whisper_model: event.target.value })}
            options={[option('tiny'), option('base'), option('small')]}
          />
          <Btn
            small
            icon={pickedReady ? 'check' : 'dl'}
            id="vo-whisper-dl"
            disabled={busy || buttonOff}
            onClick={() => void videoOrganization.download()}
          >
            {button}
          </Btn>
        </div>
        <div className={s.progress} id="vo-whisper-progress" hidden={!downloading}>
          <div
            className={s.progressBar}
            id="vo-whisper-progress-bar"
            style={st ? { width: `${downloading ? Math.min(100, pct) : 0}%` } : undefined}
          />
        </div>
        <div className={s.fieldNote} id="vo-whisper-status">
          {statusText}
        </div>
        <div className={s.lineError} id="vo-whisper-error" hidden={!whisperError} role="alert">
          {whisperError}
        </div>
      </Row>
      <Row
        sub
        label="Enable local transcription"
        htmlFor="vo-whisper-enabled"
        off={!!st && !usable}
        help={
          st && !usable
            ? 'Download a model first (and install faster-whisper on the server).'
            : 'Whisper is pinned last in the chain while this is on.'
        }
      >
        <Switch
          id="vo-whisper-enabled"
          checked={!!form.whisper_enabled}
          // off until the picked model is downloaded and the engine is there;
          // never switched off here, so a later save keeps what was asked for
          disabled={busy || (!!st && !usable)}
          onChange={(event) => void videoOrganization.toggleWhisper(event.target.checked)}
        />
      </Row>
    </>
  );
}

export function VideoSubtitles() {
  const { form, loads, langsError, job } = useVideoOrganization();
  const langs = useField('subtitle_langs');
  // the ignore switches only mean something while embedded subs count
  const ignoresOff = loads > 0 && !form.subtitle_use_embedded;
  return (
    <VoBlock>
      <SectionLabel title="Which subtitles" />
      <Card>
        <Check
          id="vo-subs-dl"
          field="download_subtitles"
          label="Download subtitles"
          help="External .srt files next to the video, from the providers below, only for what's missing."
          more="On by default for new imports. Free tiers are limited (about 20 downloads a day each), so anything missed is tried again later."
        />
        <Row
          field
          label="Subtitle languages"
          htmlFor="vo-sub-langs"
          help="Language codes in order, like en,es. Each is 2 or 3 letters, separated by commas, spaces or semicolons."
          more="One .srt per language is written as <video>.<lang>.srt."
        >
          <TextField
            id="vo-sub-langs"
            placeholder="en"
            invalid={!!langsError}
            value={langs.value}
            onChange={(event) => langs.onChange(event.target.value)}
          />
          <div className={s.lineError} id="vo-sub-langs-error" hidden={!langsError} role="alert">
            {langsError}
          </div>
        </Row>
        <Row
          label="Hearing-impaired preference"
          htmlFor="vo-sub-hi-forced"
          help="A tiebreak only, never a filter: when two subtitles score the same, the hearing-impaired one (sound cues, speaker names) wins."
        >
          <Segmented
            id="vo-sub-hi-forced"
            label="Hearing-impaired preference"
            value={String(form.subtitle_hi_forced || 'off')}
            options={[
              { value: 'off', label: 'Off, best match wins' },
              { value: 'prefer_hi', label: 'Prefer hearing-impaired' },
            ]}
            onChange={(value) => save('subtitle_hi_forced', value)}
          />
        </Row>
        <Check
          id="vo-sub-forced"
          field="subtitle_forced"
          label="Also grab forced subtitles"
          help="A forced track (the lines for scenes in another language) per language, as <video>.<lang>.forced.srt."
          more="For when you watch without full subtitles. Many titles have none, so these often stay missing."
        />
      </Card>

      <SectionLabel
        title="Already have it when"
        hint="What the video already has: a sidecar next to it, and its own embedded tracks"
      />
      <Card>
        <Check
          id="vo-sub-use-embedded"
          field="subtitle_use_embedded"
          label="Count embedded subtitles"
          help="A sidecar (.en.srt, .eng.srt, .English.sdh.srt, or an unlabeled .srt whose language is detected) always counts."
        />
        <Check
          sub
          id="vo-sub-ignore-pgs"
          field="subtitle_ignore_pgs"
          label="…except PGS (Blu-ray image subs)"
          disabled={ignoresOff}
        />
        <Check
          sub
          id="vo-sub-ignore-vobsub"
          field="subtitle_ignore_vobsub"
          label="…except VobSub (DVD image subs)"
          disabled={ignoresOff}
        />
        <Check
          sub
          id="vo-sub-ignore-ass"
          field="subtitle_ignore_ass"
          label="…except ASS/SSA (styled subs)"
          disabled={ignoresOff}
          help="Ignore image subs if your players can't show them (or transcode for them) and you'd rather have a text .srt."
        />
      </Card>

      <SectionLabel
        title="Providers"
        hint="Tried from the top until one has subtitles. API keys are on Connections"
      />
      <Card>
        <div className={s.cardPad}>
          <SubtitleChain />
        </div>
        <Whisper />
        <Check
          id="vo-sub-autosync"
          field="subtitle_autosync"
          label="Auto-sync subtitles to audio"
          help="After a download, the timing is shifted to match the video's own audio (ffsubsync, offline). A failed sync keeps the original."
          more="Needs the ffsubsync Python package on the server."
        />
        <More
          label="More options"
          count={5}
          names="match score, daily quota, upgrade threshold, retry timing"
        >
          <Row
            field
            label="Minimum match score"
            htmlFor="vo-sub-min-score"
            help="A candidate scoring below this is a miss and never downloads."
          >
            <Num id="vo-sub-min-score" field="subtitle_min_score" min="0" max="200" />
          </Row>
          <Row
            field
            label="Daily quota per provider"
            htmlFor="vo-sub-daily-quota"
            help="The most subtitle downloads per provider per day (UTC)."
          >
            <Num id="vo-sub-daily-quota" field="subtitle_daily_quota" min="1" max="1000" />
          </Row>
          <Row
            field
            label="Upgrade threshold"
            htmlFor="vo-sub-upgrade-delta"
            help="A new candidate must beat the current subtitle by this much to replace it."
          >
            <Num id="vo-sub-upgrade-delta" field="subtitle_upgrade_min_delta" min="0" max="200" />
          </Row>
          <Row
            field
            label="Retry every"
            htmlFor="vo-sub-worker-interval"
            help="How often the wanted loop tries misses again."
          >
            <Num
              id="vo-sub-worker-interval"
              field="subtitle_worker_interval_min"
              min="5"
              max="1440"
              unit="minutes"
            />
          </Row>
          <Row
            field
            label="Retries per cycle"
            htmlFor="vo-sub-worker-batch"
            help="The most wanted rows tried in one worker cycle."
          >
            <Num id="vo-sub-worker-batch" field="subtitle_worker_batch" min="1" max="200" />
          </Row>
        </More>
      </Card>

      <SectionLabel title="Videos already in your library" />
      <Card>
        <Row
          label="Missing Subtitles job"
          help={<span id="vo-sub-job-status">{jobLine(job) || 'On the Tools page'}</span>}
          more="New imports are covered on their own. The Missing Subtitles job on the Tools page checks everything you already have on a schedule and queues what's missing; turn on its Dry Run to review findings first."
        >
          <Btn
            small
            id="vo-sub-open-job"
            onClick={() =>
              // Tools opens the job's card and lights it up from ?job=
              document.dispatchEvent(
                new CustomEvent('soulsync:video-navigate', {
                  detail: { page: 'video-tools', job: MISSING_SUBS_JOB },
                }),
              )
            }
          >
            Open on Tools
          </Btn>
        </Row>
      </Card>
    </VoBlock>
  );
}
