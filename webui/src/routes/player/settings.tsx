/**
 * player settings. two scopes, stated plainly:
 *   * profile (synced across every screen): sharing what you play,
 *     autoplaying the next episode, when to ask if you're still watching
 *   * this screen (kept in this browser): video quality, picture shape,
 *     brightness, background trailers, subtitle look, music visuals
 * every change saves immediately.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute, Link, useRouteContext } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';

import { THEMES } from '@/lib/visualizer';

import type { CommercialEra, CommercialMode, PlayerSettings } from './-player.api';

import {
  CROSSFADE_OPTIONS,
  PREFETCH_OPTIONS,
  setCrossfadeSeconds,
  setPrefetchCount,
  setSmartGapless,
  useCrossfadeSeconds,
  usePrefetchCount,
  useSmartGapless,
} from './-engine/crossfade';
import { QUALITIES, type Quality } from './-engine/engine';
import {
  clearCommercialsCache,
  commercialsStatusQuery,
  playerSettingsQuery,
  savePlayerSettings,
  warmCommercials,
} from './-player.api';
import {
  CheckIcon,
  ChevronIcon,
  HomeIcon,
  MovieIcon,
  MusicIcon,
  PlayIcon,
  TvIcon,
} from './-ui/icons';
import { PageTitle } from './-ui/kit';
import { QUALITY_LABELS } from './-ui/playback/controls';
import {
  BRIGHTNESS_STEPS,
  FITS,
  setBrightness,
  setFit,
  useBrightness,
  useFit,
} from './-ui/playback/use-picture';
import { getEngine, usePlayer } from './-ui/playback/use-player';
import {
  setSubtitleStyle,
  useSubtitleStyle,
  type SubtitleSize,
} from './-ui/playback/use-subtitle-style';
import { setVisual, useVisual } from './-ui/playback/visuals';
import s from './-ui/social.module.css';
import { useTrailersSetting } from './-ui/trailer';

export const Route = createFileRoute('/player/settings')({
  component: SettingsPage,
});

function Switch({
  on,
  label,
  busy,
  onChange,
}: {
  on: boolean;
  label: string;
  busy?: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className={s.switch}
      disabled={busy}
      onClick={() => onChange(!on)}
      data-nav
    />
  );
}

function Row({
  label,
  help,
  stacked,
  children,
}: {
  label: string;
  help?: string;
  stacked?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={stacked ? `${s.row} ${s.rowStacked}` : s.row}>
      <span className={s.rowText}>
        <span className={s.rowLabel}>{label}</span>
        {help ? <span className={s.rowHelp}>{help}</span> : null}
      </span>
      {children}
    </div>
  );
}

function Group({
  icon,
  title,
  badge,
  note,
  wide,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  badge: string;
  note?: string;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className={wide ? `${s.group} ${s.groupWide}` : s.group} aria-label={title}>
      <div className={s.groupHead}>
        <div className={s.groupHeadTop}>
          <span className={s.groupIcon} aria-hidden="true">
            {icon}
          </span>
          <div className={s.groupHeadText}>
            <div className={s.groupTitleRow}>
              <h2 className={s.groupTitle}>{title}</h2>
              <span className={s.scopePill}>{badge}</span>
            </div>
            {note ? <p className={s.groupNote}>{note}</p> : null}
          </div>
        </div>
      </div>
      <div className={s.groupBody}>{children}</div>
    </section>
  );
}

const ASK_AFTER = [0, 2, 3, 4, 5, 8];
const SUBTITLE_SIZES: { value: SubtitleSize; label: string }[] = [
  { value: 'small', label: 'Small' },
  { value: 'medium', label: 'Medium' },
  { value: 'large', label: 'Large' },
];
const ORDERED_BRIGHTNESS = [...BRIGHTNESS_STEPS].reverse();

function SettingsPage() {
  const { shell } = useRouteContext({ from: '/player' });
  const isAdmin = !!shell.profile?.isAdmin;
  const queryClient = useQueryClient();
  const settings = useQuery(playerSettingsQuery());
  const commStatus = useQuery({
    ...commercialsStatusQuery(),
    refetchInterval: (q) => (q.state.data?.warming ? 3000 : false),
  });
  const [warmingComm, setWarmingComm] = useState(false);
  const [clearingComm, setClearingComm] = useState(false);
  const [saved, setSaved] = useState(false);
  const timerRef = useRef<number | null>(null);

  const flashSaved = () => {
    setSaved(true);
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setSaved(false), 1800);
  };

  useEffect(
    () => () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    },
    [],
  );

  const save = useMutation({
    mutationFn: (changes: Partial<PlayerSettings>) => savePlayerSettings(changes),
    onSuccess: (next) => {
      queryClient.setQueryData(playerSettingsQuery().queryKey, (old) =>
        old ? { ...old, settings: next } : old,
      );
      void queryClient.invalidateQueries({ queryKey: ['player', 'watching-now'] });
      if (next.commercials_enabled) {
        void queryClient.invalidateQueries({ queryKey: ['player', 'commercials-status'] });
      }
      flashSaved();
    },
  });

  const player = usePlayer();
  const subs = useSubtitleStyle();
  const brightness = useBrightness();
  const fit = useFit();
  const visual = useVisual();
  const crossfadeSec = useCrossfadeSeconds();
  const smartGapless = useSmartGapless();
  const prefetchCount = usePrefetchCount();
  const [trailers, setTrailers] = useTrailersSetting();
  const mine = settings.data?.settings;
  const busy = save.isPending;
  const activeBrightness = BRIGHTNESS_STEPS.find((v) => Math.abs(v - brightness) < 0.01) ?? 1;
  const activeFit = FITS.find((f) => f.key === fit);

  const updateLocal = (fn: () => void) => {
    fn();
    flashSaved();
  };

  return (
    <>
      <div className={s.settingsHeader}>
        <div>
          <PageTitle>Player settings</PageTitle>
          <p className={s.settingsLead}>
            Profile preferences follow you to every screen. Display preferences stay on this device.
          </p>
        </div>
        <span className={s.savedBadge} data-visible={saved ? 'true' : undefined} role="status">
          <CheckIcon /> Saved
        </span>
      </div>

      <div className={s.settingsGrid}>
        <Group
          icon={<PlayIcon />}
          title="Playback & profile"
          badge="All screens"
          note="Synced with your SoulSync profile."
        >
          <Row
            label="Share what I'm playing"
            help="Show your live session on Watching now so others can join you."
          >
            <Switch
              label="Share what I'm playing"
              on={mine?.share_activity ?? true}
              busy={!mine || busy}
              onChange={(v) => save.mutate({ share_activity: v })}
            />
          </Row>

          <Row
            label="Autoplay next episode"
            help="Automatically start the next episode when one finishes."
          >
            <Switch
              label="Autoplay next episode"
              on={mine?.autoplay_next ?? true}
              busy={!mine || busy}
              onChange={(v) => save.mutate({ autoplay_next: v })}
            />
          </Row>

          <Row
            stacked
            label="Still watching check"
            help="Pause and ask after consecutive episodes with no activity."
          >
            <div className={s.segGroup} role="radiogroup" aria-label="Ask if I'm still watching">
              {ASK_AFTER.map((n) => {
                const on = (mine?.still_watching_after ?? 3) === n;
                return (
                  <button
                    key={n}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    className={s.segBtn}
                    data-on={on ? 'true' : undefined}
                    disabled={!mine || busy}
                    onClick={() => save.mutate({ still_watching_after: n })}
                    data-nav
                  >
                    {n === 0 ? 'Never' : `${n} eps`}
                  </button>
                );
              })}
            </div>
          </Row>
        </Group>

        <Group
          icon={<MovieIcon />}
          title="Retro commercial breaks"
          badge="All screens"
          note="Optional vintage TV spots from Archive.org (auto-cached in the background, zero manual downloading) or your local commercials folder between Live Guide shows."
        >
          <Row
            label="Play retro commercials between shows"
            help={
              commStatus.data?.warming
                ? 'Fetching retro spots from Archive.org in the background…'
                : commStatus.data?.total_count
                  ? `${commStatus.data.total_count} retro spots ready (${commStatus.data.cache_mb} / ${commStatus.data.max_cache_mb ?? 500} MB cached).`
                  : 'Off by default. Turn on to automatically cache vintage 15–60s spots (max 500 MB).'
            }
          >
            <Switch
              label="Play retro commercials between shows"
              on={mine?.commercials_enabled ?? false}
              busy={!mine || busy}
              onChange={(v) => save.mutate({ commercials_enabled: v })}
            />
          </Row>

          {mine?.commercials_enabled ? (
            <>
              <Row
                stacked
                label="Commercial era"
                help="Auto matches each channel's decade or vibe (e.g. 90s channels get 90s spots)."
              >
                <div className={s.segGroup} role="radiogroup" aria-label="Commercial era">
                  {(
                    [
                      ['auto', 'Auto'],
                      ['70s', '70s'],
                      ['80s', '80s'],
                      ['90s', '90s'],
                      ['2000s', '2000s'],
                      ['kids', 'Kids'],
                      ['mix', 'Mix'],
                    ] as [CommercialEra, string][]
                  ).map(([val, lbl]) => {
                    const on = (mine?.commercials_era ?? 'auto') === val;
                    return (
                      <button
                        key={val}
                        type="button"
                        role="radio"
                        aria-checked={on}
                        className={s.segBtn}
                        data-on={on ? 'true' : undefined}
                        disabled={!mine || busy}
                        onClick={() => save.mutate({ commercials_era: val })}
                        data-nav
                      >
                        {lbl}
                      </button>
                    );
                  })}
                </div>
              </Row>

              <Row
                stacked
                label="Break style"
                help="Classic cable (default) plays 2–3 spots between every show (with a 5-min cooldown); Short plays 1–2 quick spots."
              >
                <div className={s.segGroup} role="radiogroup" aria-label="Break style">
                  {(
                    [
                      ['cable', 'Classic cable'],
                      ['short', 'Short (1–2 spots)'],
                    ] as [CommercialMode, string][]
                  ).map(([val, lbl]) => {
                    const on = (mine?.commercials_mode ?? 'cable') === val;
                    return (
                      <button
                        key={val}
                        type="button"
                        role="radio"
                        aria-checked={on}
                        className={s.segBtn}
                        data-on={on ? 'true' : undefined}
                        disabled={!mine || busy}
                        onClick={() => save.mutate({ commercials_mode: val })}
                        data-nav
                      >
                        {lbl}
                      </button>
                    );
                  })}
                </div>
              </Row>

              <Row
                label="Commercial cache"
                help="Fetch fresh vintage spots from Internet Archive (capped at 500 MB) or clear the server cache."
              >
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button
                    type="button"
                    className={s.segBtn}
                    disabled={warmingComm || commStatus.data?.warming}
                    onClick={() => {
                      setWarmingComm(true);
                      void warmCommercials(mine?.commercials_era ?? 'mix')
                        .then(() =>
                          queryClient.invalidateQueries({ queryKey: ['player', 'commercials-status'] }),
                        )
                        .finally(() => setWarmingComm(false));
                    }}
                    data-nav
                  >
                    {warmingComm || commStatus.data?.warming ? 'Fetching…' : 'Fetch spots now'}
                  </button>
                  {isAdmin ? (
                    <button
                      type="button"
                      className={s.segBtn}
                      disabled={clearingComm || !(commStatus.data?.cached_count ?? 0)}
                      onClick={() => {
                        setClearingComm(true);
                        void clearCommercialsCache()
                          .then(() =>
                            queryClient.invalidateQueries({ queryKey: ['player', 'commercials-status'] }),
                          )
                          .finally(() => setClearingComm(false));
                      }}
                      data-nav
                    >
                      {clearingComm ? 'Clearing…' : 'Clear cache'}
                    </button>
                  ) : null}
                </div>
              </Row>
            </>
          ) : null}
        </Group>

        <Group
          icon={<MovieIcon />}
          title="Poster cards"
          badge="All screens"
          note="Choose what badges and metadata appear on movie and TV cards across every device."
        >
          <Row
            label="Show movie ratings"
            help="Display the star rating pill in the bottom-right corner of movie posters."
          >
            <Switch
              label="Show movie ratings"
              on={mine?.show_movie_ratings ?? false}
              busy={!mine || busy}
              onChange={(v) => save.mutate({ show_movie_ratings: v })}
            />
          </Row>

          <Row
            label="Show episode count on TV shows"
            help="Display the episode count pill in the corner of TV show posters."
          >
            <Switch
              label="Show episode count on TV shows"
              on={mine?.show_tv_episode_count ?? true}
              busy={!mine || busy}
              onChange={(v) => save.mutate({ show_tv_episode_count: v })}
            />
          </Row>

          <Row
            label="Show release year on cards"
            help="Display the release year beneath movie and TV show posters."
          >
            <Switch
              label="Show release year on cards"
              on={mine?.show_year_on_cards ?? true}
              busy={!mine || busy}
              onChange={(v) => save.mutate({ show_year_on_cards: v })}
            />
          </Row>

          <Row
            label="Accent color"
            help="Your profile's signature highlight color across Desktop and mobile."
          >
            <div
              style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}
              role="radiogroup"
              aria-label="Accent color"
            >
              {(
                [
                  ['emerald', '#1db954', 'Emerald'],
                  ['violet', '#8b5cf6', 'Violet'],
                  ['ocean', '#38bdf8', 'Ocean'],
                  ['amber', '#f59e0b', 'Amber'],
                  ['rose', '#f43f5e', 'Rose'],
                  ['crimson', '#ef4444', 'Crimson'],
                ] as const
              ).map(([id, hex, label]) => {
                const active = (mine?.accent_color ?? 'emerald') === id;
                return (
                  <button
                    key={id}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    aria-label={label}
                    title={label}
                    disabled={!mine || busy}
                    onClick={() => save.mutate({ accent_color: id })}
                    style={{
                      width: 28,
                      height: 28,
                      borderRadius: '50%',
                      background: hex,
                      border: active ? '2.5px solid #fff' : '2px solid rgba(255,255,255,0.2)',
                      boxShadow: active ? `0 0 0 3px ${hex}55` : 'none',
                      cursor: 'pointer',
                      padding: 0,
                    }}
                    data-nav
                  />
                );
              })}
            </div>
          </Row>

          {save.isError ? (
            <div className={s.saveError} role="alert">
              Couldn't save that change — try again in a moment.
            </div>
          ) : null}
        </Group>

        <Group
          icon={<MovieIcon />}
          title="Subtitles"
          badge="This screen"
          note="Preview updates live as you adjust."
        >
          <div className={s.subPreview} aria-hidden="true">
            <div
              className={s.subPreviewScene}
              style={{ opacity: Math.max(0.35, activeBrightness) }}
            />
            <span
              className={s.subPreviewText}
              data-size={subs.size}
              data-box={subs.box ? 'true' : undefined}
            >
              The night is darkest just before the dawn.
            </span>
          </div>

          <Row stacked label="Subtitle size">
            <div className={s.segGroup} role="radiogroup" aria-label="Subtitle size">
              {SUBTITLE_SIZES.map((opt) => {
                const on = subs.size === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    className={s.segBtn}
                    data-on={on ? 'true' : undefined}
                    onClick={() => updateLocal(() => setSubtitleStyle({ size: opt.value }))}
                    data-nav
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </Row>

          <Row label="Dark backing box" help="Improves contrast over bright scenes.">
            <Switch
              label="Dark box behind subtitles"
              on={subs.box}
              onChange={(v) => updateLocal(() => setSubtitleStyle({ box: v }))}
            />
          </Row>
        </Group>

        <Group
          wide
          icon={<TvIcon />}
          title="Video & display"
          badge="This screen"
          note="Tailored for this browser, TV, or phone."
        >
          <Row
            stacked
            label="Video quality"
            help="Lower plays smoother on slow connections. Original plays directly whenever possible."
          >
            <div className={s.choiceGrid} role="radiogroup" aria-label="Video quality">
              {QUALITIES.map((q) => {
                const on = player.quality === q;
                return (
                  <button
                    key={q}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    className={s.choiceCard}
                    data-on={on ? 'true' : undefined}
                    onClick={() => updateLocal(() => getEngine().setQuality(q as Quality))}
                    data-nav
                  >
                    <span className={s.choiceTitle}>{QUALITY_LABELS[q].label}</span>
                    <span className={s.choiceDetail}>{QUALITY_LABELS[q].detail}</span>
                  </button>
                );
              })}
            </div>
          </Row>

          <Row stacked label="Picture shape" help={activeFit?.detail}>
            <div className={s.segGroup} role="radiogroup" aria-label="Picture shape">
              {FITS.map((f) => {
                const on = fit === f.key;
                return (
                  <button
                    key={f.key}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    className={s.segBtn}
                    data-on={on ? 'true' : undefined}
                    onClick={() => updateLocal(() => setFit(f.key))}
                    data-nav
                  >
                    {f.label}
                  </button>
                );
              })}
            </div>
          </Row>

          <Row
            stacked
            label="Screen brightness"
            help="Dims the video picture for watching in the dark."
          >
            <div className={s.segGroup} role="radiogroup" aria-label="Brightness">
              {ORDERED_BRIGHTNESS.map((v) => {
                const on = Math.abs(v - activeBrightness) < 0.01;
                return (
                  <button
                    key={v}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    className={s.segBtn}
                    data-on={on ? 'true' : undefined}
                    onClick={() => updateLocal(() => setBrightness(v))}
                    data-nav
                  >
                    {v === 1 ? 'Full' : `${Math.round(v * 100)}%`}
                  </button>
                );
              })}
            </div>
          </Row>

          <Row
            label="Background trailers"
            help="Automatically play muted trailers behind movie and show detail pages."
          >
            <Switch
              label="Background trailers"
              on={trailers}
              onChange={(v) => updateLocal(() => setTrailers(v))}
            />
          </Row>
        </Group>

        <Group
          wide
          icon={<MusicIcon />}
          title="Music & home"
          badge="This screen"
          note="Crossfade, lookahead caching, visuals, and your home dashboard."
        >
          <Row
            stacked
            label="Crossfade"
            help="Equal-power blend between songs on playlists, shuffles, and Artist Mixes."
          >
            <div className={s.segGroup} role="radiogroup" aria-label="Crossfade">
              {CROSSFADE_OPTIONS.map((sec) => {
                const on = crossfadeSec === sec;
                return (
                  <button
                    key={sec}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    className={s.segBtn}
                    data-on={on ? 'true' : undefined}
                    onClick={() => updateLocal(() => setCrossfadeSeconds(sec))}
                    data-nav
                  >
                    {sec === 0 ? 'Off' : `${sec}s`}
                  </button>
                );
              })}
            </div>
          </Row>

          <Row
            label="Smart album gapless"
            help="Automatically disable crossfade when consecutive tracks come from the same album."
          >
            <Switch
              label="Smart album gapless"
              on={smartGapless}
              onChange={(v) => updateLocal(() => setSmartGapless(v))}
            />
          </Row>

          <Row
            stacked
            label="Pre-cache upcoming songs"
            help="Silently pre-loads the next queued tracks so crossfades and skips start instantaneously."
          >
            <div className={s.segGroup} role="radiogroup" aria-label="Pre-cache upcoming songs">
              {PREFETCH_OPTIONS.map((cnt) => {
                const on = prefetchCount === cnt;
                return (
                  <button
                    key={cnt}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    className={s.segBtn}
                    data-on={on ? 'true' : undefined}
                    onClick={() => updateLocal(() => setPrefetchCount(cnt))}
                    data-nav
                  >
                    {cnt === 0 ? 'Off' : `Next ${cnt}`}
                  </button>
                );
              })}
            </div>
          </Row>

          <Row
            stacked
            label="Music visuals"
            help="Animated backdrop shown in full-screen Now Playing."
          >
            <div className={s.chipWrap} role="radiogroup" aria-label="Music visuals">
              {THEMES.map((t) => {
                const on = visual === t.id;
                return (
                  <button
                    key={t.id}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    className={s.themeChip}
                    data-on={on ? 'true' : undefined}
                    onClick={() => updateLocal(() => setVisual(t.id as typeof visual))}
                    data-nav
                  >
                    {t.name}
                  </button>
                );
              })}
            </div>
          </Row>

          <Row
            label="Home dashboard layout"
            help="Show, hide, reorder, and restyle your home rails for this screen or across all devices."
          >
            <Link to="/player" search={{ customize: true }} className={s.actionLink} data-nav>
              <HomeIcon />
              <span>Customize Home</span>
              <ChevronIcon />
            </Link>
          </Row>
        </Group>
      </div>
    </>
  );
}
