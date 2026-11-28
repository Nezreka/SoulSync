/**
 * the "you" sections. playback, look & feel and volume levelling save with
 * the profile (every screen); picture, subtitles and the rest of music stay
 * in this browser.
 */

import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState, type CSSProperties } from 'react';

import { THEMES } from '@/lib/visualizer';

import {
  CROSSFADE_OPTIONS,
  PREFETCH_OPTIONS,
  setCrossfadeSeconds,
  setPrefetchCount,
  setSmartGapless,
  useCrossfadeSeconds,
  usePrefetchCount,
  useSmartGapless,
} from '../../-engine/crossfade';
import { QUALITIES, type Quality } from '../../-engine/engine';
import { levelPrefsFrom, type LevelMode } from '../../-engine/loudness';
import { playerSettingsQuery, type PlayerAccentColor, type SkipMode } from '../../-player.api';
import { ChevronIcon, HomeIcon } from '../icons';
import { styles as k } from '../kit';
import { QUALITY_LABELS } from '../playback/controls';
import {
  BRIGHTNESS_STEPS,
  FITS,
  setBrightness,
  setFit,
  useBrightness,
  useFit,
} from '../playback/use-picture';
import { getEngine, usePlayer } from '../playback/use-player';
import {
  setSubtitleStyle,
  useSubtitleStyle,
  type SubtitleSize,
} from '../playback/use-subtitle-style';
import { setVisual, useVisual } from '../playback/visuals';
import { ACCENT_PALETTE } from '../shell';
import { useTrailersSetting } from '../trailer';
import { Card, Choices, Row, SaveError, Switch, useSaved, useSavePlayerSettings } from './kit';
import p from './settings.module.css';

const SKIP_CHOICES: { value: SkipMode; label: string }[] = [
  { value: 'ask', label: 'Ask' },
  { value: 'auto', label: 'Automatically' },
  { value: 'never', label: 'Never' },
];
const SKIP_ROWS: {
  key: 'skip_intro' | 'skip_credits' | 'skip_recap';
  label: string;
  help: string;
}[] = [
  {
    key: 'skip_intro',
    label: 'Skip intros',
    help: 'Ask shows a Skip intro button. Automatically skips after a short countdown, with a way back; the first episode you start still plays its intro once.',
  },
  {
    key: 'skip_credits',
    label: 'Skip credits',
    help: 'Automatically jumps past them, to a scene after the credits when there is one.',
  },
  {
    key: 'skip_recap',
    label: 'Skip recaps',
    help: 'The "previously on" at the start of an episode.',
  },
];
const ASK_AFTER = [0, 2, 3, 4, 5, 8].map((n) => ({
  value: n,
  label: n === 0 ? 'Never' : `${n} eps`,
}));
const SUBTITLE_SIZES: { value: SubtitleSize; label: string }[] = [
  { value: 'small', label: 'Small' },
  { value: 'medium', label: 'Medium' },
  { value: 'large', label: 'Large' },
];
const ORDERED_BRIGHTNESS = [...BRIGHTNESS_STEPS].reverse();
const ACCENTS: { id: PlayerAccentColor; label: string }[] = [
  { id: 'emerald', label: 'Emerald' },
  { id: 'violet', label: 'Violet' },
  { id: 'ocean', label: 'Ocean' },
  { id: 'amber', label: 'Amber' },
  { id: 'rose', label: 'Rose' },
  { id: 'crimson', label: 'Crimson' },
];

/** a browser-only change: make it, then say saved */
function useLocal() {
  const onSaved = useSaved();
  return (fn: () => void) => {
    fn();
    onSaved();
  };
}

function useActiveBrightness() {
  const brightness = useBrightness();
  return BRIGHTNESS_STEPS.find((v) => Math.abs(v - brightness) < 0.01) ?? 1;
}

export function PlaybackSection() {
  const mine = useQuery(playerSettingsQuery()).data?.settings;
  const save = useSavePlayerSettings();
  const busy = !mine || save.isPending;
  return (
    <Card>
      <Row label="Autoplay next episode" help="Start the next episode by itself when one finishes.">
        <Switch
          label="Autoplay next episode"
          on={mine?.autoplay_next ?? true}
          busy={busy}
          onChange={(v) => save.mutate({ autoplay_next: v })}
        />
      </Row>
      <Row
        label="Play next episode at credits"
        help="Up next counts down when the credits start, not in the last seconds. A scene after the credits holds it."
      >
        <Switch
          label="Play next episode at credits"
          on={mine?.next_at_credits ?? true}
          busy={busy}
          onChange={(v) => save.mutate({ next_at_credits: v })}
        />
      </Row>
      {SKIP_ROWS.map((row) => (
        <Row key={row.key} stacked label={row.label} help={row.help}>
          <Choices
            label={row.label}
            options={SKIP_CHOICES}
            value={mine?.[row.key] ?? 'ask'}
            disabled={busy}
            onPick={(v) => save.mutate({ [row.key]: v })}
          />
        </Row>
      ))}
      <Row
        stacked
        label="Still watching check"
        help="Pause and ask after this many episodes in a row with no activity."
      >
        <Choices
          label="Ask if I'm still watching"
          options={ASK_AFTER}
          value={mine?.still_watching_after ?? 3}
          disabled={busy}
          onPick={(n) => save.mutate({ still_watching_after: n })}
        />
      </Row>
      <Row
        label="Share what I'm playing"
        help="Show your live session on Watching now so others can join you."
      >
        <Switch
          label="Share what I'm playing"
          on={mine?.share_activity ?? true}
          busy={busy}
          onChange={(v) => save.mutate({ share_activity: v })}
        />
      </Row>
      <SaveError show={save.isError} />
    </Card>
  );
}

/** a wide picture on a 16:9 screen, shaped and dimmed like the player will */
function PicturePreview({ fit, brightness }: { fit: string; brightness: number }) {
  return (
    <figure className={p.preview} aria-hidden="true">
      <span className={p.previewTag}>Preview</span>
      <div className={p.screen}>
        <div
          className={p.picture}
          data-fit={fit}
          style={{ '--dim': String(brightness) } as CSSProperties}
        >
          <span className={p.pictureSun} />
          <span className={p.pictureHills} />
        </div>
      </div>
    </figure>
  );
}

export function PictureSection() {
  const player = usePlayer();
  const fit = useFit();
  const activeBrightness = useActiveBrightness();
  const [trailers, setTrailers] = useTrailersSetting();
  const local = useLocal();
  const activeFit = FITS.find((f) => f.key === fit);
  return (
    <>
      <PicturePreview fit={fit} brightness={activeBrightness} />
      <Card>
        <Row
          stacked
          label="Video quality"
          help="Lower plays smoother on slow connections. Original plays directly whenever it can."
        >
          <div className={p.choiceGrid} role="radiogroup" aria-label="Video quality">
            {QUALITIES.map((q) => {
              const on = player.quality === q;
              return (
                <button
                  key={q}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  className={p.choiceCard}
                  data-on={on ? 'true' : undefined}
                  onClick={() => local(() => getEngine().setQuality(q as Quality))}
                  data-nav
                >
                  <span className={p.choiceTitle}>{QUALITY_LABELS[q].label}</span>
                  <span className={p.choiceDetail}>{QUALITY_LABELS[q].detail}</span>
                </button>
              );
            })}
          </div>
        </Row>
        <Row stacked label="Picture shape" help={activeFit?.detail}>
          <Choices
            label="Picture shape"
            options={FITS.map((f) => ({ value: f.key, label: f.label }))}
            value={fit}
            onPick={(v) => local(() => setFit(v))}
          />
        </Row>
        <Row stacked label="Screen brightness" help="Dims the picture for watching in the dark.">
          <Choices
            label="Brightness"
            options={ORDERED_BRIGHTNESS.map((v) => ({
              value: v,
              label: v === 1 ? 'Full' : `${Math.round(v * 100)}%`,
            }))}
            value={activeBrightness}
            onPick={(v) => local(() => setBrightness(v))}
          />
        </Row>
        <Row label="Background trailers" help="Play muted trailers behind movie and show pages.">
          <Switch
            label="Background trailers"
            on={trailers}
            onChange={(v) => local(() => setTrailers(v))}
          />
        </Row>
      </Card>
    </>
  );
}

export function SubtitlesSection() {
  const subs = useSubtitleStyle();
  const activeBrightness = useActiveBrightness();
  const local = useLocal();
  return (
    <>
      <figure className={p.preview} aria-hidden="true">
        <span className={p.previewTag}>Preview</span>
        <div className={p.subScene} style={{ opacity: Math.max(0.35, activeBrightness) }} />
        <span className={p.subText} data-size={subs.size} data-box={subs.box ? 'true' : undefined}>
          The night is darkest just before the dawn.
        </span>
      </figure>
      <Card>
        <Row stacked label="Subtitle size">
          <Choices
            label="Subtitle size"
            options={SUBTITLE_SIZES}
            value={subs.size}
            onPick={(v) => local(() => setSubtitleStyle({ size: v }))}
          />
        </Row>
        <Row label="Dark backing box" help="Easier to read over bright scenes.">
          <Switch
            label="Dark box behind subtitles"
            on={subs.box}
            onChange={(v) => local(() => setSubtitleStyle({ box: v }))}
          />
        </Row>
      </Card>
    </>
  );
}

const LEVEL_MODES: { value: LevelMode; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'track', label: 'Track' },
  { value: 'album', label: 'Album' },
  { value: 'smart', label: 'Smart' },
];
const LEVEL_MODE_HELP: Record<LevelMode, string> = {
  off: 'Songs play as loud as they were mastered.',
  track: 'Every song plays at the same loudness.',
  album:
    'Each album plays at the same loudness, and its quiet and loud songs stay as the artist made them.',
  smart:
    "Album levelling while an album plays in order, song levelling on shuffle, playlists and mixes. Best of both: you never touch the volume, and albums aren't flattened.",
};
const TARGETS = [
  { value: -14, label: 'Loud' },
  { value: -16, label: 'Louder' },
  { value: -18, label: 'Standard' },
  { value: -20, label: 'Softer' },
  { value: -23, label: 'Quiet' },
];
const PREAMP_MIN = -12;
const PREAMP_MAX = 12;

function signedDb(db: number) {
  return `${db > 0 ? '+' : db < 0 ? '−' : ''}${Math.abs(db).toFixed(1)} dB`;
}

/**
 * the pre-amp: heard as it's dragged (the engine glides to it), saved once
 * the hand stops
 */
function PreampSlider({
  value,
  disabled,
  onSave,
}: {
  value: number;
  disabled?: boolean;
  onSave: (db: number) => void;
}) {
  const [draft, setDraft] = useState(value);
  const timer = useRef<number | null>(null);
  useEffect(() => setDraft(value), [value]);
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );
  const change = (db: number) => {
    setDraft(db);
    getEngine().configureLevelling({ preamp: db });
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => onSave(db), 450);
  };
  return (
    <div className={p.preamp}>
      <input
        className={p.preampRange}
        type="range"
        min={PREAMP_MIN}
        max={PREAMP_MAX}
        step={0.5}
        value={draft}
        disabled={disabled}
        aria-label="Pre-amp"
        aria-valuetext={signedDb(draft)}
        onChange={(e) => change(Number(e.target.value))}
        onDoubleClick={() => change(0)}
        data-nav
      />
      <output className={p.preampValue} aria-hidden="true">
        {signedDb(draft)}
      </output>
    </div>
  );
}

export function LevellingSection() {
  const mine = useQuery(playerSettingsQuery()).data?.settings;
  const save = useSavePlayerSettings();
  const busy = !mine || save.isPending;
  const prefs = levelPrefsFrom(mine);
  const off = prefs.mode === 'off';
  return (
    <Card label="Volume levelling">
      <Row stacked label="Volume levelling" help={LEVEL_MODE_HELP[prefs.mode]}>
        <Choices
          label="Volume levelling"
          options={LEVEL_MODES}
          value={prefs.mode}
          disabled={busy}
          onPick={(v) => save.mutate({ loudness_mode: v })}
        />
      </Row>
      <Row
        stacked
        label="Target loudness"
        help={`Levelled songs play at ${prefs.target} LUFS. Standard (-18) is the ReplayGain reference and leaves room for loud moments; Loud matches streaming apps.`}
      >
        <Choices
          label="Target loudness"
          options={TARGETS}
          value={prefs.target}
          disabled={busy || off}
          onPick={(v) => save.mutate({ loudness_target: v })}
        />
      </Row>
      <Row
        stacked
        label="Pre-amp"
        help="A little more or less on top of levelling. Double-click to reset."
      >
        <PreampSlider
          value={prefs.preamp}
          disabled={busy || (off && !prefs.video)}
          onSave={(db) => save.mutate({ loudness_preamp: db })}
        />
      </Row>
      <Row
        label="Prevent clipping"
        help="Never turns a song up past the point its loudest moment would distort."
      >
        <Switch
          label="Prevent clipping"
          on={prefs.preventClipping}
          busy={busy}
          onChange={(v) => save.mutate({ loudness_prevent_clipping: v })}
        />
      </Row>
      <Row
        label="Level video loudness"
        help="Brings very quiet films and very loud videos to the same level, so the remote stays put. Measured in the background; a video not measured yet plays as it is."
      >
        <Switch
          label="Level video loudness"
          on={prefs.video}
          busy={busy}
          onChange={(v) => save.mutate({ video_loudness: v })}
        />
      </Row>
      <SaveError show={save.isError} />
    </Card>
  );
}

export function MusicSection() {
  const visual = useVisual();
  const crossfadeSec = useCrossfadeSeconds();
  const smartGapless = useSmartGapless();
  const prefetchCount = usePrefetchCount();
  const local = useLocal();
  return (
    <>
      <LevellingSection />
      <h3 className={p.subhead}>Playback</h3>
      <Card>
        <Row
          stacked
          label="Crossfade"
          help="An equal-power blend between songs on playlists, shuffles and Artist Mixes."
        >
          <Choices
            label="Crossfade"
            options={CROSSFADE_OPTIONS.map((sec) => ({
              value: sec,
              label: sec === 0 ? 'Off' : `${sec}s`,
            }))}
            value={crossfadeSec}
            onPick={(v) => local(() => setCrossfadeSeconds(v))}
          />
        </Row>
        <Row
          label="Smart album gapless"
          help="No crossfade when the next song comes from the same album."
        >
          <Switch
            label="Smart album gapless"
            on={smartGapless}
            onChange={(v) => local(() => setSmartGapless(v))}
          />
        </Row>
        <Row
          stacked
          label="Pre-cache upcoming songs"
          help="Loads the next songs in the queue ahead, so crossfades and skips start at once."
        >
          <Choices
            label="Pre-cache upcoming songs"
            options={PREFETCH_OPTIONS.map((cnt) => ({
              value: cnt,
              label: cnt === 0 ? 'Off' : `Next ${cnt}`,
            }))}
            value={prefetchCount}
            onPick={(v) => local(() => setPrefetchCount(v))}
          />
        </Row>
        <Row stacked label="Music visuals" help="The moving backdrop in full-screen Now Playing.">
          <div className={p.chipWrap} role="radiogroup" aria-label="Music visuals">
            {THEMES.map((t) => {
              const on = visual === t.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  className={p.themeChip}
                  data-on={on ? 'true' : undefined}
                  onClick={() => local(() => setVisual(t.id as typeof visual))}
                  data-nav
                >
                  {t.name}
                </button>
              );
            })}
          </div>
        </Row>
      </Card>
    </>
  );
}

/** two poster cards in the player's own card style, showing what the switches show */
function PosterPreview({
  ratings,
  episodes,
  year,
}: {
  ratings: boolean;
  episodes: boolean;
  year: boolean;
}) {
  const cards = [
    { title: 'The Long Night', badge: ratings ? '★ 7.8' : '', sub: year ? '2019' : '', tone: 'a' },
    {
      title: 'Harbor Lights',
      badge: episodes ? '24 ep' : '',
      sub: year ? '2021 · 2 seasons' : '2 seasons',
      tone: 'b',
    },
  ];
  return (
    <figure className={`${p.preview} ${p.posterStage}`} aria-hidden="true">
      <span className={p.previewTag}>Preview</span>
      {cards.map((c) => (
        <div key={c.title} className={p.posterCard}>
          <div className={k.art}>
            <span className={p.posterArt} data-tone={c.tone} />
            {c.badge ? <span className={k.cardBadge}>{c.badge}</span> : null}
          </div>
          <div className={k.cardText}>
            <div className={k.cardTitle}>{c.title}</div>
            {c.sub ? <div className={k.cardSub}>{c.sub}</div> : null}
          </div>
        </div>
      ))}
    </figure>
  );
}

export function LookSection() {
  const mine = useQuery(playerSettingsQuery()).data?.settings;
  const save = useSavePlayerSettings();
  const busy = !mine || save.isPending;
  const accent = mine?.accent_color ?? 'emerald';
  const ratings = mine?.show_movie_ratings ?? false;
  const episodes = mine?.show_tv_episode_count ?? true;
  const year = mine?.show_year_on_cards ?? true;
  return (
    <>
      <Card label="Accent color">
        <Row label="Accent color" help="Your profile's highlight color, on desktop and phone.">
          <div className={p.swatches} role="radiogroup" aria-label="Accent color">
            {ACCENTS.map(({ id, label }) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={accent === id}
                aria-label={label}
                title={label}
                className={p.swatch}
                style={{ '--swatch-rgb': ACCENT_PALETTE[id].rgb } as CSSProperties}
                disabled={busy}
                onClick={() => save.mutate({ accent_color: id })}
                data-nav
              />
            ))}
          </div>
        </Row>
      </Card>

      <h3 className={p.subhead}>Poster cards</h3>
      <PosterPreview ratings={ratings} episodes={episodes} year={year} />
      <Card label="Poster cards">
        <Row label="Show movie ratings" help="The star rating in the corner of movie posters.">
          <Switch
            label="Show movie ratings"
            on={ratings}
            busy={busy}
            onChange={(v) => save.mutate({ show_movie_ratings: v })}
          />
        </Row>
        <Row
          label="Show episode count on TV shows"
          help="The episode count in the corner of TV show posters."
        >
          <Switch
            label="Show episode count on TV shows"
            on={episodes}
            busy={busy}
            onChange={(v) => save.mutate({ show_tv_episode_count: v })}
          />
        </Row>
        <Row label="Show release year on cards" help="The year under movie and TV show posters.">
          <Switch
            label="Show release year on cards"
            on={year}
            busy={busy}
            onChange={(v) => save.mutate({ show_year_on_cards: v })}
          />
        </Row>
        <SaveError show={save.isError} />
      </Card>

      <h3 className={p.subhead}>Home</h3>
      <Card>
        <Row
          label="Home dashboard layout"
          help="Show, hide, reorder and restyle your home rows, for this screen or every device."
        >
          <Link to="/player" search={{ customize: true }} className={p.actionLink} data-nav>
            <HomeIcon />
            <span>Customize Home</span>
            <ChevronIcon />
          </Link>
        </Row>
      </Card>
    </>
  );
}
