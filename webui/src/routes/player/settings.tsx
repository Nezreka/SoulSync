/**
 * player settings. two kinds, said plainly on the page:
 *   * yours, on every screen (kept on the server): sharing what you play,
 *     the next episode playing on its own, when to ask if you're still there
 *   * this screen's (kept in this browser): video quality, how subtitles
 *     look, brightness, the picture's shape, the music visuals
 * every change saves at once.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useState } from 'react';

import { THEMES } from '@/lib/visualizer';

import type { PlayerSettings } from './-player.api';

import { QUALITIES, type Quality } from './-engine/engine';
import { playerSettingsQuery, savePlayerSettings } from './-player.api';
import { PageTitle, PlayerLink } from './-ui/kit';
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
import { setSubtitleStyle, useSubtitleStyle } from './-ui/playback/use-subtitle-style';
import { setVisual, useVisual } from './-ui/playback/visuals';
import s from './-ui/social.module.css';

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
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={s.row}>
      <span className={s.rowText}>
        <span className={s.rowLabel}>{label}</span>
        {help ? <span className={s.rowHelp}>{help}</span> : null}
      </span>
      {children}
    </div>
  );
}

function Group({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={s.group} aria-label={title}>
      <div className={s.groupHead}>
        <h2 className={s.groupTitle}>{title}</h2>
        {note ? <p className={s.groupNote}>{note}</p> : null}
      </div>
      {children}
    </section>
  );
}

const ASK_AFTER = [0, 2, 3, 4, 5, 8];

function SettingsPage() {
  const queryClient = useQueryClient();
  const settings = useQuery(playerSettingsQuery());
  const [saved, setSaved] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (changes: Partial<PlayerSettings>) => savePlayerSettings(changes),
    onSuccess: (next) => {
      queryClient.setQueryData(playerSettingsQuery().queryKey, (old) =>
        old ? { ...old, settings: next } : old,
      );
      void queryClient.invalidateQueries({ queryKey: ['player', 'watching-now'] });
      setSaved('Saved');
      window.setTimeout(() => setSaved(null), 1800);
    },
  });
  const player = usePlayer();
  const subs = useSubtitleStyle();
  const brightness = useBrightness();
  const fit = useFit();
  const visual = useVisual();
  const mine = settings.data?.settings;
  const busy = save.isPending;

  return (
    <>
      <PageTitle>Player settings</PageTitle>
      <div className={s.settings}>
        <Group title="You, on every screen" note={saved ?? 'Kept with your profile.'}>
          <Row
            label="Share what I'm playing"
            help="Others on this server see what you watch or listen to on Watching now, and can join you there. Turn it off and you're gone from it at once."
          >
            <Switch
              label="Share what I'm playing"
              on={mine?.share_activity ?? true}
              busy={!mine || busy}
              onChange={(v) => save.mutate({ share_activity: v })}
            />
          </Row>
          <Row
            label="Play the next episode on its own"
            help="Off: an episode ends and waits for you."
          >
            <Switch
              label="Play the next episode on its own"
              on={mine?.autoplay_next ?? true}
              busy={!mine || busy}
              onChange={(v) => save.mutate({ autoplay_next: v })}
            />
          </Row>
          <Row
            label="Ask if I'm still watching"
            help="After this many episodes in a row with nobody touching anything."
          >
            <select
              className={s.select}
              value={mine?.still_watching_after ?? 3}
              disabled={!mine || busy}
              onChange={(e) => save.mutate({ still_watching_after: Number(e.target.value) })}
              aria-label="Ask if I'm still watching"
            >
              {ASK_AFTER.map((n) => (
                <option key={n} value={n}>
                  {n ? `After ${n} episodes` : 'Never'}
                </option>
              ))}
            </select>
          </Row>
          {save.isError ? (
            <Row label="That didn't save" help="Try again in a moment.">
              <span />
            </Row>
          ) : null}
        </Group>

        <Group title="This screen" note="Kept in this browser, so a TV and a phone can differ.">
          <Row
            label="Video quality"
            help="Lower plays smoother on a slow connection. Original never converts unless it has to."
          >
            <select
              className={s.select}
              value={player.quality}
              onChange={(e) => getEngine().setQuality(e.target.value as Quality)}
              aria-label="Video quality"
            >
              {QUALITIES.map((q) => (
                <option key={q} value={q}>
                  {QUALITY_LABELS[q].label} · {QUALITY_LABELS[q].detail}
                </option>
              ))}
            </select>
          </Row>
          <Row label="Subtitle size">
            <select
              className={s.select}
              value={subs.size}
              onChange={(e) =>
                setSubtitleStyle({ size: e.target.value as 'small' | 'medium' | 'large' })
              }
              aria-label="Subtitle size"
            >
              <option value="small">Small</option>
              <option value="medium">Medium</option>
              <option value="large">Large</option>
            </select>
          </Row>
          <Row label="Dark box behind subtitles" help="Easier to read over a bright picture.">
            <Switch
              label="Dark box behind subtitles"
              on={subs.box}
              onChange={(v) => setSubtitleStyle({ box: v })}
            />
          </Row>
          <Row label="Brightness" help="Dims the picture, for watching in the dark.">
            <select
              className={s.select}
              value={String(BRIGHTNESS_STEPS.find((v) => Math.abs(v - brightness) < 0.01) ?? 1)}
              onChange={(e) => setBrightness(Number(e.target.value))}
              aria-label="Brightness"
            >
              {BRIGHTNESS_STEPS.map((v) => (
                <option key={v} value={String(v)}>
                  {v === 1 ? 'Full' : `${Math.round(v * 100)}%`}
                </option>
              ))}
            </select>
          </Row>
          <Row label="Picture shape" help={FITS.find((f) => f.key === fit)?.detail}>
            <select
              className={s.select}
              value={fit}
              onChange={(e) => setFit(e.target.value as typeof fit)}
              aria-label="Picture shape"
            >
              {FITS.map((f) => (
                <option key={f.key} value={f.key}>
                  {f.label}
                </option>
              ))}
            </select>
          </Row>
          <Row label="Music visuals" help="What moves behind the song in now playing.">
            <select
              className={s.select}
              value={visual}
              onChange={(e) => setVisual(e.target.value as typeof visual)}
              aria-label="Music visuals"
            >
              {THEMES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </Row>
        </Group>

        <p className={s.groupNote}>
          Your home page has its own settings:{' '}
          <PlayerLink href="/player" data-nav>
            customize it from Home
          </PlayerLink>
          .
        </p>
      </div>
    </>
  );
}
