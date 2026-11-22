/**
 * the guide's two views and its detail panel.
 *
 * grid: channels down, time across, a now line; music channels show half-hour
 * blocks instead of three-minute slivers. on now: one line per channel with
 * what is airing, how far in, and what's next. the panel above both shows the
 * picked program with its art, time left and the channel's next few, or a
 * music block's track list.
 */

import { useEffect, useRef, useState } from 'react';

import type { Channel } from '../-lib/channels';
import type { GuideBlock, GuideProgram } from '../-lib/schedule';

import { channelArt } from '../-lib/channels';
import { clock, runtime } from '../-lib/format';
import { channelQueue } from '../-lib/play-items';
import { blockArtists, mergeIntoBlocks } from '../-lib/schedule';
import { useChannelViewers, ViewerFaces, ViewerPins, ViewersHere } from './channel-viewers';
import { PlayIcon } from './icons';
import { Art, PlayButton, PlayerLink, styles as s, useProgressive } from './kit';
import { CloseIcon } from './playback/icons';
import { useReminder } from './playback/reminders';
import { tuneIn } from './playback/tuner';
import { playItems } from './playback/use-player';

export interface Picked {
  channel: Channel;
  program: GuideProgram;
  block?: GuideBlock;
}

function StarButton({
  channel,
  on,
  onToggle,
}: {
  channel: Channel;
  on: boolean;
  onToggle: (channel: Channel) => void;
}) {
  return (
    <button
      type="button"
      className={s.starBtn}
      aria-pressed={on}
      aria-label={on ? `Remove ${channel.name} from favorites` : `Add ${channel.name} to favorites`}
      title={on ? 'Favorite' : 'Add to favorites'}
      onClick={(e) => {
        e.stopPropagation();
        onToggle(channel);
      }}
      data-nav
    >
      {on ? '★' : '☆'}
    </button>
  );
}

/** a channel's picture: four of its titles, tiled */
function ChannelLogo({ channel }: { channel: Channel }) {
  const art = channelArt(channel);
  return (
    <span
      className={s.guideLogo}
      data-count={art.length >= 4 ? 4 : art.length ? 1 : 0}
      aria-hidden="true"
    >
      {(art.length >= 4 ? art.slice(0, 4) : art.slice(0, 1)).map((src) => (
        <img
          key={src}
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          onError={(e) => (e.currentTarget.style.visibility = 'hidden')}
        />
      ))}
    </span>
  );
}

function ChannelCell({
  channel,
  favorite,
  onFavorite,
  viewers,
}: {
  channel: Channel;
  favorite: boolean;
  onFavorite: (channel: Channel) => void;
  viewers?: Parameters<typeof ViewerFaces>[0]['sessions'];
}) {
  return (
    <div className={s.guideChannel} role="rowheader">
      <button
        type="button"
        className={s.guideTune}
        title={`Watch ${channel.name} now`}
        aria-label={`Tune in to ${channel.number ?? ''} ${channel.name}`}
        onClick={() => void tuneIn(channel)}
        data-nav
      >
        <ChannelLogo channel={channel} />
        <span className={s.guideNum}>
          <span className={s.guideNumText}>{channel.number}</span>
          <span className={s.guideTunePlay} aria-hidden="true">
            <PlayIcon />
          </span>
        </span>
        <span className={s.guideChannelText}>
          <span className={s.guideChannelName} title={channel.name}>
            {channel.name}
          </span>
          <span className={s.guideChannelBlurb} title={channel.blurb}>
            {channel.blurb}
          </span>
        </span>
      </button>
      <ViewerFaces sessions={viewers} />
      <StarButton channel={channel} on={favorite} onToggle={onFavorite} />
    </div>
  );
}

export function GuideGrid({
  groups,
  schedules,
  from,
  to,
  now,
  pxPerMinute,
  favorites,
  onFavorite,
  onPick,
  onPreview,
  label,
}: {
  groups: { kind: string; label: string; channels: Channel[] }[];
  schedules: Map<string, GuideProgram[]>;
  from: number;
  to: number;
  now: number;
  pxPerMinute: number;
  favorites: ReadonlySet<string>;
  onFavorite: (channel: Channel) => void;
  onPick: (picked: Picked, at?: HTMLElement) => void;
  /** a program the pointer or focus rests on: the panel above shows it */
  onPreview?: (picked: Picked) => void;
  label: string;
}) {
  // the hours shown fill the box: on a wide screen each minute gets wider
  // instead of the grid stopping short (never narrower than pxPerMinute)
  const box = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState(pxPerMinute);
  const minutes = (to - from) / 60000;
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => {
      const corner = el.querySelector<HTMLElement>('[data-guide-corner]');
      const lane = el.clientWidth - (corner?.offsetWidth ?? 0) - 2;
      setFit(Math.max(pxPerMinute, lane / minutes));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [pxPerMinute, minutes]);
  const viewers = useChannelViewers();
  const ppm = fit;
  const width = minutes * ppm;
  const x = (ms: number) => ((ms - from) / 60000) * ppm;
  const marks: number[] = [];
  for (let t = Math.ceil(from / 1_800_000) * 1_800_000; t < to; t += 1_800_000) marks.push(t);
  const nowVisible = now >= from && now < to;

  // eighty channels of cells is a lot to mount at once on a TV: rows come in
  // a couple of dozen at a time, ahead of the scroll
  const totalRows = groups.reduce((n, g) => n + g.channels.length, 0);
  const rows = useProgressive(
    totalRows,
    groups.map((g) => `${g.kind}:${g.channels.length}`).join('|'),
    24,
  );
  let budget = rows.count;
  const visibleGroups = [];
  for (const group of groups) {
    if (budget <= 0) break;
    visibleGroups.push({ ...group, channels: group.channels.slice(0, budget) });
    budget -= group.channels.length;
  }

  return (
    <div ref={box} className={s.guide} role="grid" aria-label={label}>
      <div className={s.guideCanvas} style={{ width: `calc(var(--g-chan) + ${width}px)` }}>
        <div className={s.guideTimes} role="row">
          <div className={s.guideCorner} data-guide-corner />
          <div className={s.guideLane} style={{ width }}>
            {marks.map((t) => (
              <div key={t} className={s.guideTime} style={{ left: x(t) }} role="columnheader">
                {clock(t)}
              </div>
            ))}
          </div>
        </div>
        {visibleGroups.map((group) => (
          <div key={group.kind} role="rowgroup" aria-label={group.label}>
            <div className={s.guideGroup}>{group.label}</div>
            {group.channels.map((channel) => {
              const programs = schedules.get(channel.id) ?? [];
              const cells: {
                key: string;
                start: number;
                end: number;
                picked: Picked;
                title: string;
                sub: string;
                premiere?: boolean;
              }[] = channel.music
                ? mergeIntoBlocks(programs).map((block) => ({
                    key: `${block.start}`,
                    start: block.start,
                    end: block.end,
                    picked: { channel, program: block.items[0], block },
                    // the channel's name is on the left: the cell says who's on
                    title: blockArtists(block, 2),
                    sub: `${block.items.length} songs`,
                  }))
                : programs.map((p) => ({
                    key: `${p.key}-${p.start}`,
                    start: p.start,
                    end: p.end,
                    picked: { channel, program: p },
                    title: p.title,
                    sub: p.subtitle ?? '',
                    premiere: p.premiere,
                  }));
              return (
                <div key={channel.id} className={s.guideRow} role="row">
                  <ChannelCell
                    channel={channel}
                    favorite={favorites.has(channel.id)}
                    onFavorite={onFavorite}
                    viewers={viewers.get(channel.id)}
                  />
                  <div className={s.guideLane} style={{ width }}>
                    {channel.music ? null : (
                      <ViewerPins
                        sessions={viewers.get(channel.id)}
                        programs={programs}
                        x={x}
                        width={width}
                      />
                    )}
                    {cells.map((cell) => {
                      const left = Math.max(0, x(cell.start));
                      const right = Math.min(width, x(cell.end));
                      if (right <= left) return null;
                      const live = cell.start <= now && cell.end > now;
                      const done = live ? (now - cell.start) / (cell.end - cell.start) : 0;
                      return (
                        <button
                          key={cell.key}
                          type="button"
                          role="gridcell"
                          className={s.guideProgram}
                          data-now={live ? 'true' : undefined}
                          data-past={cell.end <= now ? 'true' : undefined}
                          data-premiere={cell.premiere ? 'true' : undefined}
                          style={{ left: left + 2, width: Math.max(8, right - left - 4) }}
                          title={`${cell.premiere ? 'New: ' : ''}${cell.title}${cell.sub ? ` · ${cell.sub}` : ''}\n${clock(cell.start)} – ${clock(cell.end)}`}
                          onClick={(e) => onPick(cell.picked, e.currentTarget)}
                          onMouseEnter={() => onPreview?.(cell.picked)}
                          onFocus={() => onPreview?.(cell.picked)}
                          data-nav
                        >
                          <span className={s.guideProgramTitle}>
                            {cell.premiere ? <span className={s.guideNew}>New</span> : null}
                            {cell.title}
                          </span>
                          {right - left > 110 ? (
                            <span className={s.guideProgramSub}>
                              {cell.sub ? `${cell.sub} · ` : ''}
                              {clock(cell.start)}
                            </span>
                          ) : null}
                          {live ? (
                            <span className={s.guideLive} style={{ width: `${done * 100}%` }} />
                          ) : null}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        ))}
        {rows.count < totalRows ? (
          <div ref={rows.sentinel} className={s.gridMore} aria-hidden="true" />
        ) : null}
        {nowVisible ? (
          <div
            className={s.guideNow}
            style={{ left: `calc(var(--g-chan) + ${x(now)}px)` }}
            aria-hidden="true"
          />
        ) : null}
      </div>
    </div>
  );
}

/** what's on now, one line per channel */
export function OnNow({
  channels,
  schedules,
  now,
  favorites,
  onFavorite,
  onPick,
}: {
  channels: Channel[];
  schedules: Map<string, GuideProgram[]>;
  now: number;
  favorites: ReadonlySet<string>;
  onFavorite: (channel: Channel) => void;
  onPick: (picked: Picked, at?: HTMLElement) => void;
}) {
  return (
    <div className={s.onNow} role="list" aria-label="On now">
      {channels.map((channel) => {
        const programs = schedules.get(channel.id) ?? [];
        const index = programs.findIndex((p) => p.start <= now && p.end > now);
        if (index < 0) return null;
        const current = programs[index];
        const next = programs[index + 1];
        const block = channel.music
          ? mergeIntoBlocks(programs).find((b) => b.start <= now && b.end > now)
          : undefined;
        const done = (now - current.start) / (current.end - current.start);
        return (
          <div key={channel.id} className={s.onNowRow} role="listitem">
            <button
              type="button"
              className={s.onNowMain}
              onClick={(e) => onPick({ channel, program: current, block }, e.currentTarget)}
              data-nav
            >
              <span className={s.guideNum}>{channel.number}</span>
              <span className={s.onNowArt}>
                <Art src={current.art} alt={current.title} />
              </span>
              <span className={s.onNowText}>
                <span className={s.onNowChannel}>{channel.name}</span>
                <span className={s.onNowTitle}>{current.title}</span>
                <span className={s.rowSub}>
                  {[current.subtitle, `${runtime((current.end - now) / 1000)} left`]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
                <span className={s.onNowBar}>
                  <span className={s.progressFill} style={{ width: `${done * 100}%` }} />
                </span>
              </span>
              {next ? (
                <span className={s.onNowNext}>
                  <span className={s.ratingLabel}>Next · {clock(next.start)}</span>
                  <span className={s.onNowNextTitle}>{next.title}</span>
                </span>
              ) : null}
            </button>
            <button
              type="button"
              className={s.onNowTune}
              aria-label={`Watch ${channel.name} now`}
              title="Watch now"
              onClick={() => void tuneIn(channel)}
              data-nav
            >
              <PlayIcon />
            </button>
            <StarButton channel={channel} on={favorites.has(channel.id)} onToggle={onFavorite} />
          </div>
        );
      })}
    </div>
  );
}

/** remind me when this starts: kept for the profile, rung on whichever screen is open */
function RemindButton({
  channel,
  program,
  start,
}: {
  channel: Channel;
  program: GuideProgram;
  start: number;
}) {
  const { on, toggle } = useReminder(channel.id, Math.round(start / 1000));
  return (
    <button
      type="button"
      className={s.btn}
      aria-pressed={on}
      onClick={() =>
        void toggle(
          { key: program.key, title: program.title, subtitle: program.subtitle },
          channel.name,
        )
      }
      data-nav
    >
      {on ? 'Reminder set' : 'Remind me'}
    </button>
  );
}

/** the picked program, its art, how far in, and what comes after */
export function GuideDetail({
  picked,
  schedules,
  now,
  favorite,
  onFavorite,
  onRemoveRule,
  onEditRule,
  onClose,
}: {
  picked: Picked;
  schedules: Map<string, GuideProgram[]>;
  now: number;
  favorite: boolean;
  onFavorite: (channel: Channel) => void;
  onRemoveRule?: () => void;
  /** a channel someone made: change what's on it */
  onEditRule?: () => void;
  onClose: () => void;
}) {
  const { channel, program, block } = picked;
  const start = block?.start ?? program.start;
  const end = block?.end ?? program.end;
  const live = start <= now && end > now;
  const later = (schedules.get(channel.id) ?? []).filter((p) => p.start >= end).slice(0, 3);
  const airing = block?.items.find((p) => p.start <= now && p.end > now) ?? program;
  const channelPrograms = schedules.get(channel.id) ?? [];
  // live: what's airing and the channel after it; from the start: this program (or set) and on
  const liveQueue = channelQueue(channelPrograms, airing);
  const startQueue = channelQueue(channelPrograms, block ? block.items[0] : program);
  const viewers = useChannelViewers().get(channel.id);

  return (
    <section className={s.guideDetail} aria-label="Program">
      <button
        type="button"
        className={s.guideDetailClose}
        aria-label="Close"
        title="Close (Esc)"
        onClick={onClose}
        data-nav
      >
        <CloseIcon />
      </button>
      <div className={s.guideDetailArt}>
        <Art src={(block ? airing : program).art} alt={program.title} />
      </div>
      <div className={s.guideDetailBody}>
        <span className={s.heroKicker}>
          {channel.number} · {channel.name} · {clock(start)} – {clock(end)}
          {live ? ' · on now' : ''}
        </span>
        <span className={s.guideDetailTitle}>
          {program.premiere ? <span className={s.guideNew}>New</span> : null}
          {block ? `${channel.name} mix` : program.title}
        </span>
        <span className={s.rowSub}>
          {block
            ? `${block.items.length} songs · ${blockArtists(block, 4)}`
            : [program.subtitle, runtime(program.seconds)].filter(Boolean).join(' · ')}
        </span>
        {live ? (
          <span className={s.onNowBar}>
            <span
              className={s.progressFill}
              style={{ width: `${((now - start) / (end - start)) * 100}%` }}
            />
          </span>
        ) : null}
        <div className={s.heroActions}>
          {live && liveQueue.length ? (
            <PlayButton
              label="Join live"
              onPlay={() => {
                // in at the minute it's on, through the tuner: channel up and down work from here
                tuneIn(channel);
                onClose();
              }}
            />
          ) : null}
          <PlayButton
            label={block ? 'Play this set' : 'Watch from the start'}
            primary={!live}
            onPlay={
              startQueue.length
                ? () => {
                    playItems(startQueue);
                    onClose();
                  }
                : undefined
            }
          />
          {!block && program.href ? (
            <PlayerLink href={program.href} className={s.btn} data-nav>
              Details
            </PlayerLink>
          ) : null}
          <button
            type="button"
            className={s.btn}
            aria-pressed={favorite}
            aria-label={
              favorite
                ? `Remove ${channel.name} from favorites`
                : `Add ${channel.name} to favorites`
            }
            title={favorite ? 'A favorite channel' : 'Add to favorites'}
            onClick={() => onFavorite(channel)}
            data-nav
          >
            {favorite ? '★' : '☆'}
          </button>
          {start > now ? (
            <RemindButton
              channel={channel}
              program={block ? block.items[0] : program}
              start={start}
            />
          ) : null}
          {onEditRule ? (
            <button type="button" className={s.btn} onClick={onEditRule} data-nav>
              Change channel
            </button>
          ) : null}
          {onRemoveRule ? (
            <button type="button" className={s.btn} onClick={onRemoveRule} data-nav>
              Remove channel
            </button>
          ) : null}
        </div>
        <ViewersHere
          channel={channel}
          sessions={viewers}
          programs={channelPrograms}
          onDone={onClose}
        />
      </div>
      <div className={s.guideDetailSide}>
        {block ? (
          <>
            <div className={s.pickerLabel}>In this set</div>
            <ol className={s.trackList}>
              {block.items.map((t) => (
                <li
                  key={`${t.key}-${t.start}`}
                  className={s.trackItem}
                  data-now={t.start <= now && t.end > now ? 'true' : undefined}
                >
                  <span className={s.trackTime}>{clock(t.start)}</span>
                  <span className={s.trackText}>
                    {t.title}
                    {t.subtitle ? <span className={s.rowSub}> · {t.subtitle}</span> : null}
                  </span>
                </li>
              ))}
            </ol>
          </>
        ) : later.length ? (
          <>
            <div className={s.pickerLabel}>Up next on {channel.name}</div>
            <ol className={s.trackList}>
              {later.map((t) => (
                <li key={`${t.key}-${t.start}`} className={s.trackItem}>
                  <span className={s.trackTime}>{clock(t.start)}</span>
                  <span className={s.trackText}>
                    {t.title}
                    {t.subtitle ? <span className={s.rowSub}> · {t.subtitle}</span> : null}
                  </span>
                </li>
              ))}
            </ol>
          </>
        ) : null}
      </div>
    </section>
  );
}
