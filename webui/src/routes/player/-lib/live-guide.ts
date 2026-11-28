/**
 * Adapts /api/live/channels and /api/live/programs into standard Guide Channel
 * objects so real Live TV channels sit alongside virtual library channels.
 */

import type { Channel } from './channels';
import type { LiveChannel, LiveProgram } from './live';

export function liveChannelToGuideChannel(
  ch: LiveChannel,
  programs: readonly LiveProgram[],
): Channel {
  const pool = programs.map((p, idx) => ({
    key: `live:${ch.id}:${p.start_ts}:${idx}`,
    title: p.title || ch.display_name,
    subtitle: [p.episode_num, p.subtitle].filter(Boolean).join(' · ') || undefined,
    seconds: Math.max(60, Math.round(p.end_ts - p.start_ts)),
    href: `/player/guide?filter=all`,
    art: p.icon_url || ch.logo_url || null,
    kind: 'movie' as const,
  }));

  const parsedNum = Number.parseFloat(ch.guide_number || '');
  return {
    id: `live:${ch.id}`,
    name: ch.display_name || ch.name,
    kind: 'live',
    blurb: [ch.guide_number ? `Ch ${ch.guide_number}` : null, ch.group_title || 'Live TV']
      .filter(Boolean)
      .join(' · '),
    pool,
    liveChannelId: ch.id,
    number: Number.isFinite(parsedNum) ? parsedNum : undefined,
  };
}
