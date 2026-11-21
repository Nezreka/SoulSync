/**
 * The pages the guard looks at. A control is recorded under the first
 * scenario whose page has it and where changing it sends something. The two
 * baselines are the recorded payloads as they are, one per side; the rest lay
 * a few values on to draw, or wake, what only shows for them.
 */

import type { Scenario } from './engine';

import { type Dict, ENDPOINTS } from './backend';

/** the admin's row of /api/profiles with a PIN and a login password set */
function securedProfiles() {
  const answer = ENDPOINTS['/api/profiles'] as Dict;
  return {
    ...answer,
    profiles: (answer.profiles as Dict[]).map((profile) =>
      profile.id === 1 ? { ...profile, has_pin: true, has_password: true } : profile,
    ),
  };
}

export const SCENARIOS: Scenario[] = [
  { name: 'music', side: 'music', why: 'the recorded payloads, music side' },
  { name: 'video', side: 'video', why: 'the recorded payloads, video side' },
  {
    name: 'jellyfin',
    side: 'music',
    why: 'Jellyfin as the music server (its user and library pickers), and everything a switch elsewhere keeps locked: Spotify tags on, explicit tracks allowed, an admin PIN and password set, the per-indexer seeding rows loaded',
    settings: {
      active_media_server: 'jellyfin',
      spotify: { embed_tags: true },
      content_filter: { allow_explicit: true },
    },
    endpoints: { '/api/profiles': securedProfiles() },
    clicks: ['video-seed-idx-refresh'],
  },
  {
    name: 'navidrome',
    side: 'music',
    why: 'Navidrome as the music server (its music folder picker)',
    settings: { active_media_server: 'navidrome' },
  },
];
