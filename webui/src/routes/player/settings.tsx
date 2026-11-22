/**
 * player settings. two scopes, stated plainly:
 *   * profile (synced across every screen): sharing what you play,
 *     autoplaying the next episode, when to ask if you're still watching
 *   * this screen (kept in this browser): video quality, picture shape,
 *     brightness, background trailers, subtitle look, music visuals
 * every change saves immediately.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute, Link } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';

import { THEMES } from '@/lib/visualizer';

import type { PlayerSettings } from './-player.api';

import { QUALITIES, type Quality } from './-engine/engine';
import { playerSettingsQuery, savePlayerSettings } from './-player.api';
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
  const queryClient = useQueryClient();
  const settings = useQuery(playerSettingsQuery());
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
      flashSaved();
    },
  });

  const player = usePlayer();
  const subs = useSubtitleStyle();
  const brightness = useBrightness();
  const fit = useFit();
  const visual = useVisual();
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
          note="Visual ambiance and your home dashboard."
        >
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
