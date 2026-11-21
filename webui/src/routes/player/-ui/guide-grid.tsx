/**
 * the guide's two views and its detail panel.
 *
 * grid: channels down, time across, a now line; music channels show half-hour
 * blocks instead of three-minute slivers. on now: one line per channel with
 * what is airing, how far in, and what's next. the panel above both shows the
 * picked program with its art, time left and the channel's next few, or a
 * music block's track list.
 */

import type { Channel } from '../-lib/channels';
import type { GuideBlock, GuideProgram } from '../-lib/schedule';

import { clock, runtime } from '../-lib/format';
import { blockArtists, mergeIntoBlocks } from '../-lib/schedule';
import { Art, PlayButton, PlayerLink, styles as s, useProgressive } from './kit';

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

function ChannelCell({
  channel,
  favorite,
  onFavorite,
}: {
  channel: Channel;
  favorite: boolean;
  onFavorite: (channel: Channel) => void;
}) {
  return (
    <div className={s.guideChannel} role="rowheader">
      <span className={s.guideNum}>{channel.number}</span>
      <span className={s.guideChannelText}>
        <span className={s.guideChannelName}>{channel.name}</span>
        <span className={s.guideChannelBlurb}>{channel.blurb}</span>
      </span>
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
  onPick: (picked: Picked) => void;
  label: string;
}) {
  const width = ((to - from) / 60000) * pxPerMinute;
  const x = (ms: number) => ((ms - from) / 60000) * pxPerMinute;
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
    <div className={s.guide} role="grid" aria-label={label}>
      <div className={s.guideCanvas} style={{ width: `calc(var(--g-chan) + ${width}px)` }}>
        <div className={s.guideTimes} role="row">
          <div className={s.guideCorner} />
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
              }[] = channel.music
                ? mergeIntoBlocks(programs).map((block) => ({
                    key: `${block.start}`,
                    start: block.start,
                    end: block.end,
                    picked: { channel, program: block.items[0], block },
                    title: `${channel.name} mix`,
                    sub: blockArtists(block),
                  }))
                : programs.map((p) => ({
                    key: `${p.key}-${p.start}`,
                    start: p.start,
                    end: p.end,
                    picked: { channel, program: p },
                    title: p.title,
                    sub: p.subtitle ?? '',
                  }));
              return (
                <div key={channel.id} className={s.guideRow} role="row">
                  <ChannelCell
                    channel={channel}
                    favorite={favorites.has(channel.id)}
                    onFavorite={onFavorite}
                  />
                  <div className={s.guideLane} style={{ width }}>
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
                          style={{ left: left + 2, width: Math.max(8, right - left - 4) }}
                          title={`${cell.title}${cell.sub ? ` · ${cell.sub}` : ''}\n${clock(cell.start)} – ${clock(cell.end)}`}
                          onClick={() => onPick(cell.picked)}
                          data-nav
                        >
                          <span className={s.guideProgramTitle}>{cell.title}</span>
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
  onPick: (picked: Picked) => void;
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
              onClick={() => onPick({ channel, program: current, block })}
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
            <StarButton channel={channel} on={favorites.has(channel.id)} onToggle={onFavorite} />
          </div>
        );
      })}
    </div>
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
  onClose,
}: {
  picked: Picked;
  schedules: Map<string, GuideProgram[]>;
  now: number;
  favorite: boolean;
  onFavorite: (channel: Channel) => void;
  onRemoveRule?: () => void;
  onClose: () => void;
}) {
  const { channel, program, block } = picked;
  const start = block?.start ?? program.start;
  const end = block?.end ?? program.end;
  const live = start <= now && end > now;
  const later = (schedules.get(channel.id) ?? []).filter((p) => p.start >= end).slice(0, 3);
  const airing = block?.items.find((p) => p.start <= now && p.end > now) ?? program;

  return (
    <section className={s.guideDetail} aria-live="polite" aria-label="Program">
      <div className={s.guideDetailArt}>
        <Art src={(block ? airing : program).art} alt={program.title} />
      </div>
      <div className={s.guideDetailBody}>
        <span className={s.heroKicker}>
          {channel.number} · {channel.name} · {clock(start)} – {clock(end)}
          {live ? ' · on now' : ''}
        </span>
        <span className={s.guideDetailTitle}>{block ? `${channel.name} mix` : program.title}</span>
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
          {live ? <PlayButton label="Join live" /> : null}
          <PlayButton label="Watch from the start" primary={!live} />
          {!block && program.href ? (
            <PlayerLink href={program.href} className={s.btn} data-nav>
              Details
            </PlayerLink>
          ) : null}
          <button type="button" className={s.btn} onClick={() => onFavorite(channel)} data-nav>
            {favorite ? '★ Favorite' : '☆ Add to favorites'}
          </button>
          {onRemoveRule ? (
            <button type="button" className={s.btn} onClick={onRemoveRule} data-nav>
              Remove channel
            </button>
          ) : null}
          <button type="button" className={s.btn} onClick={onClose} data-nav>
            Close
          </button>
        </div>
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
