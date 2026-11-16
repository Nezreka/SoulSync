/**
 * The Organization card's Video panel (SETTINGS_PORT.md, video pass V2b): the
 * path templates with their live previews, what happens when a file finishes,
 * the recycle bin, the YouTube switches, and the Subtitles group (the provider
 * chain, Whisper, the advanced knobs, the Missing Subtitles job). Was
 * video-settings.js; the settings are video-organization.ts. A text field
 * saves when it is left, a box or a pick saves at once, as before.
 */

import {
  type DragEvent,
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';

import {
  EPISODE_EG,
  MOVIE_EG,
  MISSING_SUBS_JOB,
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

function Hint({ children, id }: { children?: ReactNode; id?: string }) {
  return (
    <small className="settings-hint" id={id}>
      {children}
    </small>
  );
}

const save = (key: string, value: string | boolean) =>
  void videoOrganization.change({ [key]: value });

function Check({
  id,
  field,
  label,
  disabled,
  dim,
}: {
  id: string;
  field: string;
  label: ReactNode;
  disabled?: boolean;
  dim?: boolean;
}) {
  const { form } = useVideoOrganization();
  return (
    <label className="checkbox-label" style={dim ? { opacity: 0.45 } : undefined}>
      <input
        type="checkbox"
        id={id}
        checked={!!form[field]}
        disabled={disabled}
        onChange={(event) => save(field, event.target.checked)}
      />
      {'\n'}
      <span>{label}</span>
    </label>
  );
}

/** a field typed in: the value kept, saved when it is left (the panel hears it) */
function useField(field: string) {
  const { form } = useVideoOrganization();
  return {
    value: String(form[field] ?? ''),
    onChange: (event: { target: { value: string } }) =>
      videoOrganization.edit({ [field]: event.target.value }),
  };
}

function TextField(props: {
  id: string;
  field: string;
  type?: string;
  placeholder?: string;
  min?: string;
  max?: string;
  step?: string;
}) {
  const { field, type = 'text', ...rest } = props;
  return <input type={type} {...rest} {...useField(field)} />;
}

function Select({
  id,
  field,
  className,
  children,
  label,
}: {
  id: string;
  field: string;
  className?: string;
  children: ReactNode;
  label?: string;
}) {
  const { form } = useVideoOrganization();
  return (
    <select
      id={id}
      className={className}
      aria-label={label}
      value={String(form[field] ?? '')}
      onChange={(event) => save(field, event.target.value)}
    >
      {children}
    </select>
  );
}

/** the subtitle provider chain, as the download chain looks and works */
function SubtitleChain({ busy }: { busy: boolean }) {
  const { order, whisper, loads } = useVideoOrganization();
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [listOver, setListOver] = useState(false);
  const [poolOver, setPoolOver] = useState(false);

  const commit = (next: string[]) => videoOrganization.setOrder(next);
  const add = (id: string) => {
    if (!order.includes(id)) commit([...order, id]);
  };
  const move = (id: string, delta: number) => {
    const next = order.slice();
    const i = next.indexOf(id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    commit(next);
  };
  const remove = (id: string) => {
    // an empty chain finds no subtitles, with nothing saying why
    if (order.length <= 1) {
      window.showToast?.(
        'Keep at least one provider — subtitles need somewhere to come from',
        'info',
      );
      return;
    }
    commit(order.filter((x) => x !== id));
  };

  const dragged = (event: DragEvent) => event.dataTransfer?.getData('text/plain') || dragging;
  const startDrag = (id: string) => (event: DragEvent) => {
    setDragging(id);
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', id);
    }
  };
  const endDrag = () => {
    setDragging(null);
    setOver(null);
    setListOver(false);
    setPoolOver(false);
  };
  const overOn = (key: string) => (event: DragEvent) => {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    setOver(key);
  };
  const leave = (key: string) => () => setOver((o) => (o === key ? null : o));

  const available = SUBCHAIN_NETWORK_IDS.filter((id) => !order.includes(id));
  // Whisper is pinned last while it is on, downloaded and has its engine
  const whisperOn = !!(whisper && whisper.enabled && whisper.downloaded && whisper.engine);

  const tile = (id: string) => {
    const m = subchainMeta(id);
    return (
      <button
        key={id}
        type="button"
        className={dragging === id ? 'dlchain-tile dragging' : 'dlchain-tile'}
        draggable
        data-src={id}
        data-action="add"
        title={`Add ${m.name} to the chain`}
        onDragStart={startDrag(id)}
        onDragEnd={endDrag}
        onClick={() => add(id)}
      >
        <span className="dlchain-tile-art">
          <span className="emoji-icon">{m.emoji}</span>
        </span>
        <span className="dlchain-tile-name">{m.name}</span>
      </button>
    );
  };

  const step = (id: string, position: number, total: number, locked: boolean) => {
    const m = subchainMeta(id);
    const role = locked
      ? 'Last resort'
      : position === 1
        ? 'Tried first'
        : `Fallback ${position - 1}`;
    const key = `step:${id}`;
    const art = (
      <>
        <span className="dlchain-step-rank">{position}</span>
        <span className="dlchain-step-art">
          <span className="emoji-icon">{m.emoji}</span>
        </span>
        <span className="dlchain-step-text">
          <span className="dlchain-step-name">{m.name}</span>
          <span className="dlchain-step-role">{role}</span>
        </span>
      </>
    );
    if (locked)
      return (
        <div
          key={id}
          className="dlchain-step"
          data-src={id}
          title={`${m.name} - ${role}`}
          aria-label={`${m.name}, ${role}`}
        >
          {art}
        </div>
      );
    return (
      <div
        key={id}
        className={[
          'dlchain-step',
          dragging === id ? 'dragging' : '',
          over === key ? 'drag-over' : '',
        ]
          .filter(Boolean)
          .join(' ')}
        data-src={id}
        title={`${m.name} - ${role}`}
        aria-label={`${m.name}, ${role}`}
        draggable
        onDragStart={startDrag(id)}
        onDragEnd={endDrag}
        onDragOver={overOn(key)}
        onDragLeave={leave(key)}
        onDrop={(event) => {
          // dropping on a step puts the dragged one before it
          event.preventDefault();
          event.stopPropagation();
          setOver(null);
          const src = dragged(event);
          if (!src || src === id) return;
          const next = order.filter((x) => x !== src);
          const at = next.indexOf(id);
          next.splice(at < 0 ? next.length : at, 0, src);
          commit(next);
        }}
      >
        <span className="dlchain-grip" aria-hidden="true">
          {'⠿'}
        </span>
        {art}
        <span className="dlchain-step-move">
          <button
            type="button"
            className="dlchain-move"
            title={`Move ${m.name} up`}
            aria-label={`Move ${m.name} up`}
            disabled={busy || position === 1}
            data-action="up"
            onClick={() => move(id, -1)}
          >
            {'▲'}
          </button>
          <button
            type="button"
            className="dlchain-move"
            title={`Move ${m.name} down`}
            aria-label={`Move ${m.name} down`}
            disabled={busy || position === total}
            data-action="down"
            onClick={() => move(id, 1)}
          >
            {'▼'}
          </button>
        </span>
        <button
          type="button"
          className="dlchain-btn"
          title={`Remove ${m.name} from the chain`}
          aria-label={`Remove ${m.name} from the chain`}
          data-action="remove"
          onClick={() => remove(id)}
        >
          {'×'}
        </button>
      </div>
    );
  };

  const steps = order.map((id, i) => step(id, i + 1, order.length, false));
  if (whisperOn) steps.push(step('whisper', order.length + 1, order.length + 1, true));
  const flow: ReactNode[] = [];
  steps.forEach((s, i) => {
    if (i > 0) flow.push(<div key={`c${i}`} className="dlchain-connector" />);
    flow.push(s);
  });
  if (steps.length) flow.push(<div key="c-end" className="dlchain-connector" />);
  const last = order.length ? subchainMeta(order[order.length - 1]).name : '';

  return (
    <div id="subchain-widget">
      <div className="dlchain-columns">
        <div className="dlchain-col">
          <div className="dlchain-col-head">
            <span className="dlchain-col-label">Available</span>
            <span className="dlchain-col-hint">drag one across, or click it</span>
          </div>
          <div
            className={poolOver ? 'dlchain-pool drag-over' : 'dlchain-pool'}
            id="subchain-pool"
            onDragOver={(event) => {
              event.preventDefault();
              setPoolOver(true);
            }}
            onDragLeave={() => setPoolOver(false)}
            onDrop={(event) => {
              // dragging a step back to the pool removes it
              event.preventDefault();
              setPoolOver(false);
              const src = dragged(event);
              if (src && order.includes(src)) remove(src);
            }}
          >
            {/* nothing is drawn until the settings load, as before */}
            {!loads ? null : available.length ? (
              available.map(tile)
            ) : (
              <div className="dlchain-empty">Every provider is in the chain.</div>
            )}
          </div>
        </div>
        <div className="dlchain-col">
          <div className="dlchain-col-head">
            <span className="dlchain-col-label">Provider chain</span>
            <span className="dlchain-col-hint" id="subchain-mode-hint">
              {!loads
                ? null
                : order.length > 1
                  ? `chain — ${order.length} providers`
                  : order.length === 1
                    ? 'single provider'
                    : 'nothing selected'}
            </span>
          </div>
          <div
            className={listOver ? 'dlchain-list drag-over' : 'dlchain-list'}
            id="subchain-list"
            onDragOver={(event) => {
              event.preventDefault();
              setListOver(true);
            }}
            onDragLeave={() => setListOver(false)}
            onDrop={(event) => {
              // anywhere else in the chain column appends
              event.preventDefault();
              setListOver(false);
              const src = dragged(event);
              if (!src || order.includes(src)) return;
              commit([...order, src]);
            }}
          >
            {loads ? flow : null}
            {!loads ? null : (
              <div
                className={[
                  'dlchain-slot',
                  order.length ? '' : 'first',
                  over === 'slot' ? 'drag-over' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                id="subchain-slot"
                onDragOver={overOn('slot')}
                onDragLeave={leave('slot')}
                onDrop={(event) => {
                  // the slot at the end: always last
                  event.preventDefault();
                  event.stopPropagation();
                  setOver(null);
                  const src = dragged(event);
                  if (!src) return;
                  commit([...order.filter((x) => x !== src), src]);
                }}
              >
                <span className="dlchain-slot-title">
                  {order.length
                    ? `Drag a provider here — tried after ${last}`
                    : 'Drag a provider here — this one is tried first'}
                </span>
                <span className="dlchain-slot-hint">
                  {order.length
                    ? 'It only runs if everything above it came up empty.'
                    : 'Subtitles need at least one provider.'}
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Whisper: the model, its download, and the switch that needs both */
function Whisper({ busy }: { busy: boolean }) {
  const { whisper: st, form, whisperError } = useVideoOrganization();
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
      button = `✓ ${picked} downloaded`;
      buttonOff = true;
    } else button = `Download ${picked} model`;
  }

  let status = '';
  if (st) {
    const bits: string[] = [];
    if (st.downloaded)
      bits.push(
        `Downloaded: ${st.model} (${whisperBytes(st.size_bytes)})${st.downloaded_at ? ` on ${String(st.downloaded_at).slice(0, 10)}` : ''}.`,
      );
    else bits.push('No model downloaded yet.');
    if (st.disk_model && st.disk_model !== picked)
      bits.push(`Note: ${st.disk_model} is on disk — downloading ${picked} replaces it.`);
    if (!st.engine)
      bits.push(
        `Transcription engine missing on the server: ${st.engine_hint || 'install faster-whisper'}.`,
      );
    status = bits.join(' ');
  }
  const option = (value: string) => {
    const meta = st?.models?.[value];
    return <option value={value}>{meta ? `${meta.label} — ${meta.blurb}` : value}</option>;
  };

  return (
    <div className="form-group">
      <label>Local transcription (Whisper):</label>
      <div className="vo-whisper-row">
        <select
          id="vo-whisper-model"
          aria-label="Whisper model"
          value={picked}
          onChange={(event) => videoOrganization.edit({ whisper_model: event.target.value })}
        >
          {option('tiny')}
          {option('base')}
          {option('small')}
        </select>
        {'\n'}
        <button
          className="test-button"
          id="vo-whisper-dl"
          type="button"
          disabled={busy || buttonOff}
          onClick={() => void videoOrganization.download()}
        >
          {button}
        </button>
      </div>
      <div className="vo-whisper-progress" id="vo-whisper-progress" hidden={!downloading}>
        <div
          className="vo-whisper-progress-bar"
          id="vo-whisper-progress-bar"
          style={st ? { width: `${downloading ? Math.min(100, pct) : 0}%` } : undefined}
        />
      </div>
      <Hint id="vo-whisper-status">{status}</Hint>
      <small
        className="settings-hint"
        id="vo-whisper-error"
        hidden={!whisperError}
        style={{ color: '#ff6b6b' }}
      >
        {whisperError}
      </small>
      <label className="checkbox-label">
        <input
          type="checkbox"
          id="vo-whisper-enabled"
          checked={!!form.whisper_enabled}
          // off until the picked model is downloaded and the engine is there;
          // never unticked, so a later save keeps what was asked for
          disabled={busy || (!!st && !usable)}
          title={
            st && !usable
              ? 'Download a model first (and install faster-whisper on the server).'
              : undefined
          }
          onChange={(event) => void videoOrganization.toggleWhisper(event.target.checked)}
        />
        {'\n'}
        <span>Enable local transcription</span>
      </label>
      <Hint>
        Last-resort fallback: when no provider has subtitles, this server transcribes the
        video&apos;s own audio &mdash; no API key, no quota, fully offline after the one-time model
        download. Transcription runs in the background subtitle worker (a 45-minute episode takes
        roughly 10&ndash;20 minutes on a typical CPU). Requires the <code>faster-whisper</code>{' '}
        Python package on the server.
      </Hint>
    </div>
  );
}

export function VideoOrganizationPanel({ hidden }: { hidden: boolean }) {
  const { form, status, loads, langsError, job } = useVideoOrganization();
  // an object ref: the gate and the change listener read it in effects
  const panel = useRef<HTMLDivElement | null>(null);
  const busy = status === 'loading' || status === 'failed';

  // a text field left saves (the languages are checked first)
  useEffect(() => {
    const el = panel.current;
    if (!el) return;
    const left = (event: Event) => {
      const id = (event.target as Element | null)?.id || '';
      if (id === 'vo-sub-langs') videoOrganization.commitLangs();
      else if (SAVED_WHEN_LEFT[id]) void videoOrganization.save();
    };
    el.addEventListener('change', left);
    return () => el.removeEventListener('change', left);
  }, []);

  // while it loads (or failed to) every control is off but Retry, and back
  // as it was after, as loadSharedLibrary did it
  useLayoutEffect(() => {
    const el = panel.current;
    if (!el || !busy) return;
    const off = [
      ...el.querySelectorAll<HTMLInputElement>(
        'input, select, textarea, button:not(.stg-library-retry)',
      ),
    ].filter((c) => !c.disabled);
    for (const c of off) c.disabled = true;
    return () => {
      for (const c of off) c.disabled = false;
    };
  }, [busy]);

  const colons = String(form.colon_replacement || 'delete');
  const preview = (field: string, placeholder: string, eg: Record<string, string>, ext: string) =>
    loads ? renderTemplate(String(form[field] || '') || placeholder, eg, colons) + ext : '…';
  const MOVIE_PH = '$title ($year)/$title ($year) $quality';
  const EPISODE_PH = '$series/Season $season/$series - S$seasonE$episode - $episodetitle $quality';
  const YOUTUBE_PH = '$channel/Season $year/$channel - $sxe - $title';
  // the ignore switches only mean something while embedded subs count
  const ignoresOff = loads > 0 && !form.subtitle_use_embedded;

  return (
    <div
      id="organization-video-panel"
      role="tabpanel"
      aria-labelledby="organization-video-tab"
      className="stg-media-panel"
      hidden={hidden}
      tabIndex={0}
      aria-busy={status === 'idle' ? undefined : busy ? 'true' : 'false'}
      ref={panel}
    >
      {busy ? (
        <p className="stg-library-load-status settings-hint" role="status">
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
      <div className="stg-media-context">
        <strong>Video organization</strong>
        <span className="stg-media-summary">Path templates, post-processing &amp; recycle</span>
      </div>

      <div className="settings-group" data-stg="library">
        <div className="form-group">
          <label>Movie Path Template:</label>
          <TextField id="vo-movie-template" field="movie_template" placeholder={MOVIE_PH} />
          <Hint>
            Variables: $title, $titlefirst, $year, $quality (e.g. Bluray-1080p), $resolution,
            $source, $codec, $edition, $audiolangs, $sublangs, $tmdbid, $imdbid. Folders split on
            &quot;/&quot;, the last segment is the filename. <code>[$audiolangs]</code> gives{' '}
            <code>[JA+EN]</code> and disappears when a file has no language tags.
          </Hint>
          <Hint>
            Example:{' '}
            <code id="vo-movie-preview">
              {preview('movie_template', MOVIE_PH, MOVIE_EG, '.mkv')}
            </code>
          </Hint>
        </div>

        <div className="form-group">
          <label>Episode Path Template:</label>
          <TextField id="vo-episode-template" field="episode_template" placeholder={EPISODE_PH} />
          <Hint>
            Variables: $series, $season (01), $seasonraw (1), $episode (01), $episodetitle, $year,
            $quality, $resolution, $source, $codec, $audiolangs, $sublangs, $tmdbid, $imdbid,
            $tvdbid (the show&apos;s ids).
          </Hint>
          <Hint>
            Example:{' '}
            <code id="vo-episode-preview">
              {preview('episode_template', EPISODE_PH, EPISODE_EG, '.mkv')}
            </code>
          </Hint>
        </div>

        <div className="form-group">
          <label htmlFor="vo-colon-replacement">Colons in titles:</label>
          <Select id="vo-colon-replacement" field="colon_replacement">
            <option value="delete">Delete (Dexter Resurrection)</option>
            <option value="smart">Smart (Dexter - Resurrection, 12:30 becomes 12-30)</option>
            <option value="space_dash_space">Space dash space (Dexter - Resurrection)</option>
            <option value="dash">Dash (Dexter- Resurrection)</option>
          </Select>
          <Hint>
            File systems don&apos;t allow a colon, so it&apos;s replaced in movie and episode names.
            Mass Rename can bring files you already have in line.
          </Hint>
        </div>

        <div className="form-group">
          <label>YouTube Path Template:</label>
          <TextField id="vo-youtube-template" field="youtube_template" placeholder={YOUTUBE_PH} />
          <Hint>
            Followed YouTube channels organize as a TV show. Variables: $channel, $year, $sxe
            (s2024e0315 &mdash; lets Plex/Jellyfin index episodes without an online agent), $date
            (2024-03-15), $month (03), $day (15), $title, $videoid.
          </Hint>
          <Hint>
            Plex tip: set the YouTube library&apos;s agent to <strong>Personal Media Shows</strong>{' '}
            &mdash; episode titles then come straight from the filename, and the channel folder gets
            its poster/fanart automatically.
          </Hint>
          <Hint>
            Example:{' '}
            <code id="vo-youtube-preview">
              {preview('youtube_template', YOUTUBE_PH, YOUTUBE_EG, '.mp4')}
            </code>
          </Hint>
        </div>

        <div className="form-group">
          <label>YouTube: videos to grab when you follow a channel:</label>
          <TextField
            id="vo-yt-follow-count"
            field="youtube_follow_count"
            type="number"
            min="0"
            max="100"
            placeholder="5"
          />
          <Hint>
            Following a channel backfills its most recent N videos, then watches for anything new
            posted after you follow. This is also the rolling &quot;keep the last N current&quot;
            net the channel scan uses. <strong>0</strong> = don&apos;t backfill anything, only grab
            new uploads going forward. It never pulls the whole back catalog.
          </Hint>
        </div>

        <div className="form-group">
          <label>When a file finishes:</label>
          <Select id="vo-transfer-mode" field="transfer_mode" className="form-select">
            <option value="copy">Copy in, then remove the source (kept for torrents)</option>
            <option value="move">Move into the library</option>
          </Select>
          <Hint>How a finished download moves from the download folder into your library.</Hint>
        </div>

        <div className="form-group">
          <Check id="vo-verify" field="verify_with_ffprobe" label="Verify with ffprobe" />
          <Hint>
            Tag by the file&apos;s real resolution and reject corrupt / sample files. Needs ffmpeg;
            falls back to the release name when absent.
          </Hint>
        </div>

        <div className="form-group">
          <Check
            id="vo-replace"
            field="replace_existing"
            label="Replace an existing copy when the new one is better"
          />
          <Hint>Deletes the old file when an upgrade lands.</Hint>
        </div>

        <div className="form-group">
          <Check id="vo-subs" field="carry_subtitles" label="Carry matching subtitles" />
          <Hint>Bring sibling .srt / .ass files alongside the video, renamed to match.</Hint>
        </div>

        <div className="form-group">
          <Check id="vo-artwork" field="save_artwork" label="Save artwork into the folder" />
          <Hint>
            Download the full artwork set &mdash; poster.jpg, fanart.jpg, clearlogo.png (plus season
            posters for shows) &mdash; next to the movie / into the show folder. Off by default;
            Plex/Jellyfin usually fetch their own once the folder is named cleanly. Turn on to pin
            specific art or for offline/local-assets setups.
          </Hint>
        </div>

        <div className="form-group">
          <Check id="vo-nfo" field="write_nfo" label="Write NFO metadata files" />
          <Hint>
            Write Kodi/Jellyfin-style movie.nfo / tvshow.nfo (title, plot, rating, genres, cast,
            ids) so the library is fully portable. Off by default &mdash; Plex ignores these, but
            Jellyfin/Kodi/Emby use them.
          </Hint>
        </div>

        <div className="form-group">
          <Check
            id="vo-recycle"
            field="recycle_deletes"
            label="Recycle deleted files instead of erasing them"
          />
          <Hint>
            Upgrade-replaced copies, retention-cleaned YouTube episodes and dismissed imports move
            into an <code>ss_recycle</code> folder inside the library instead of being deleted
            &mdash; a safety net if something gets replaced by mistake.
          </Hint>
        </div>

        <div className="form-group">
          <label>Keep recycled files for (days):</label>
          <TextField
            id="vo-recycle-days"
            field="recycle_keep_days"
            type="number"
            min="1"
            max="365"
            placeholder="7"
          />
          <Hint>
            Recycled files older than this are removed for good (cleaned automatically as new files
            are recycled).
          </Hint>
        </div>

        <div className="form-group">
          <label>Recycle folder (optional):</label>
          <TextField
            id="vo-recycle-path"
            field="recycle_path"
            placeholder="auto — ss_recycle inside each library"
          />
          <Hint>
            Set a single folder to collect everything (keep it on the same drive as your libraries
            so moves are instant). Blank = an <code>ss_recycle</code> folder per library.
          </Hint>
        </div>

        <div className="form-group">
          <label>Minimum free disk space (GB):</label>
          <TextField
            id="vo-min-free"
            field="min_free_disk_gb"
            type="number"
            min="0"
            step="1"
            placeholder="0 = off"
          />
          <Hint>
            New grabs are refused (and the wishlist drains pause) when the target drive has less
            free space than this &mdash; so a download can never fill the disk. 0 disables the
            check.
          </Hint>
        </div>

        <div className="form-group">
          <label>YouTube: SponsorBlock</label>
          <Select id="vo-sponsorblock" field="youtube_sponsorblock">
            <option value="off">Off</option>
            <option value="mark">Mark segments as chapters</option>
            <option value="remove">Cut sponsor segments out</option>
          </Select>
          <Hint>
            Uses the community SponsorBlock database at download time. <strong>Mark</strong> adds
            skippable chapters (sponsor, self-promo, intro, outro); <strong>Cut</strong> removes
            sponsor/self-promo/interaction segments from the file entirely.
          </Hint>
        </div>

        <div className="form-group">
          <Check id="vo-yt-subs" field="youtube_embed_subs" label="YouTube: embed subtitles" />
          <Hint>
            Embeds YouTube&apos;s subtitles (your Subtitle Languages above) into the downloaded file
            so Plex/Jellyfin offer them without sidecar files.
          </Hint>
        </div>

        <div className="form-group">
          <button
            className="test-button"
            id="vo-reset"
            style={{ background: '#666' }}
            onClick={() => void videoOrganization.reset()}
          >
            {'\u{1F504}'} Reset to Defaults
          </button>
          <Hint>Restores the Radarr/Sonarr standard layout.</Hint>
        </div>
      </div>

      <div className="settings-group" data-stg="library">
        <div className="post-processing-section-title">Subtitles</div>

        <div className="form-group">
          <Check id="vo-subs-dl" field="download_subtitles" label="Download subtitles" />
          <Hint>
            Fetch external .srt files next to the video from the provider chain below, only for
            what&apos;s missing. On by default for new imports &mdash; free tiers are quota-limited
            (~20 downloads/day each), so anything missed is retried later.
          </Hint>
        </div>

        <div className="form-group">
          <label>Subtitle Languages:</label>
          <TextField id="vo-sub-langs" field="subtitle_langs" placeholder="en" />
          <Hint>
            Language codes to fetch, in priority order (e.g. <code>en,es</code>). One .srt per
            language is written as &lt;video&gt;.&lt;lang&gt;.srt.
          </Hint>
          <Hint>
            Each code is 2&ndash;3 letters; separate codes with commas, spaces, or semicolons.
          </Hint>
          <small
            className="settings-hint"
            id="vo-sub-langs-error"
            hidden={!langsError}
            style={{ color: '#ff6b6b' }}
          >
            {langsError}
          </small>
        </div>

        <div className="form-group">
          <label htmlFor="vo-sub-hi-forced">Hearing-impaired preference:</label>
          <Select id="vo-sub-hi-forced" field="subtitle_hi_forced">
            <option value="off">Off — best match wins</option>
            <option value="prefer_hi">Prefer hearing-impaired</option>
          </Select>
          <Hint>
            A tiebreak only, never a filter: when two subtitles score the same, the hearing-impaired
            one (sound cues, speaker names) wins.
          </Hint>
        </div>

        <div className="form-group">
          <Check id="vo-sub-forced" field="subtitle_forced" label="Also grab forced subtitles" />
          <Hint>
            On top of the full track, fetch a forced (foreign-parts-only) track per language,
            written as &lt;video&gt;.&lt;lang&gt;.forced.srt &mdash; the lines for scenes in another
            language, for when you watch without full subtitles. Many titles have none, so these
            often stay missing.
          </Hint>
        </div>

        <div className="form-group">
          <label>Already have it when&hellip;</label>
          <Hint>
            Before fetching, SoulSync checks what the video already has: any sidecar next to it (
            <code>.en.srt</code>, <code>.eng.srt</code>, <code>.English.sdh.srt</code>, or an
            unlabeled <code>.srt</code> whose language it detects) and the file&apos;s own embedded
            tracks.
          </Hint>
          <Check
            id="vo-sub-use-embedded"
            field="subtitle_use_embedded"
            label="Count embedded subtitles"
          />
          <Check
            id="vo-sub-ignore-pgs"
            field="subtitle_ignore_pgs"
            label={<>&hellip;except PGS (Blu-ray image subs)</>}
            disabled={busy || ignoresOff}
            dim={ignoresOff}
          />
          <Check
            id="vo-sub-ignore-vobsub"
            field="subtitle_ignore_vobsub"
            label={<>&hellip;except VobSub (DVD image subs)</>}
            disabled={busy || ignoresOff}
            dim={ignoresOff}
          />
          <Check
            id="vo-sub-ignore-ass"
            field="subtitle_ignore_ass"
            label={<>&hellip;except ASS/SSA (styled subs)</>}
            disabled={busy || ignoresOff}
            dim={ignoresOff}
          />
          <Hint>
            Ignore image subs if your players can&apos;t show them (or have to transcode for them)
            and you&apos;d rather have a text .srt.
          </Hint>
        </div>

        <div className="form-group">
          <Check
            id="vo-sub-autosync"
            field="subtitle_autosync"
            label="Auto-sync subtitles to audio"
          />
          <Hint>
            After a download, shift the subtitle timing to match the video&apos;s own audio
            (ffsubsync, offline). Requires the <code>ffsubsync</code> Python package on the server;
            a failed sync keeps the original file.
          </Hint>
        </div>

        <div className="form-group">
          <label>Subtitle provider chain:</label>
          <SubtitleChain busy={busy} />
          <Hint>
            Each provider is tried in order, top first, until subtitles are found. Whisper stays
            pinned last as the last resort when enabled.
          </Hint>
          <Hint>
            API keys live under Services &rarr; OpenSubtitles, SubDL, SubSource. Embedded needs no
            key.
          </Hint>
        </div>

        <Whisper busy={busy} />

        <div className="form-group">
          <label>Advanced:</label>
          <div className="vo-sub-adv-grid">
            <div>
              <label htmlFor="vo-sub-min-score">Minimum match score</label>
              <TextField
                id="vo-sub-min-score"
                field="subtitle_min_score"
                type="number"
                min="0"
                max="200"
                step="1"
              />
              <Hint>Candidates scoring below this are a miss and never download.</Hint>
            </div>
            <div>
              <label htmlFor="vo-sub-daily-quota">Daily quota per provider</label>
              <TextField
                id="vo-sub-daily-quota"
                field="subtitle_daily_quota"
                type="number"
                min="1"
                max="1000"
                step="1"
              />
              <Hint>Max subtitle downloads per provider per day (UTC).</Hint>
            </div>
            <div>
              <label htmlFor="vo-sub-upgrade-delta">Upgrade threshold</label>
              <TextField
                id="vo-sub-upgrade-delta"
                field="subtitle_upgrade_min_delta"
                type="number"
                min="0"
                max="200"
                step="1"
              />
              <Hint>
                A new candidate must beat the current subtitle by this much to replace it.
              </Hint>
            </div>
            <div>
              <label htmlFor="vo-sub-worker-interval">Retry every (minutes)</label>
              <TextField
                id="vo-sub-worker-interval"
                field="subtitle_worker_interval_min"
                type="number"
                min="5"
                max="1440"
                step="1"
              />
              <Hint>How often the wanted loop retries misses.</Hint>
            </div>
            <div>
              <label htmlFor="vo-sub-worker-batch">Retries per cycle</label>
              <TextField
                id="vo-sub-worker-batch"
                field="subtitle_worker_batch"
                type="number"
                min="1"
                max="200"
                step="1"
              />
              <Hint>Max wanted rows attempted per worker cycle.</Hint>
            </div>
          </div>
        </div>

        <div className="form-group">
          <label>Videos already in your library:</label>
          <div>
            <button
              className="test-button"
              id="vo-sub-open-job"
              type="button"
              onClick={() =>
                // Tools opens the job's card and lights it up from ?job=
                document.dispatchEvent(
                  new CustomEvent('soulsync:video-navigate', {
                    detail: { page: 'video-tools', job: MISSING_SUBS_JOB },
                  }),
                )
              }
            >
              Missing Subtitles job &rarr;
            </button>
          </div>
          <Hint id="vo-sub-job-status">{jobLine(job)}</Hint>
          <Hint>
            New imports are covered automatically. The Missing Subtitles job on the Tools page
            checks everything you already own on a schedule and queues what&apos;s missing; turn on
            its Dry Run to review findings first.
          </Hint>
        </div>
      </div>
    </div>
  );
}
