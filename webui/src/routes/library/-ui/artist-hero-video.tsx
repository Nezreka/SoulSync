import { useQuery } from '@tanstack/react-query';
import { useCallback, useState } from 'react';

import { fetchBackdropVideo } from '@/routes/discover/-discover.backdrop';
import { useVideoBackdropsEnabled, useVideoSlot } from '@/routes/discover/-discover.video-stage';
import { BackdropVideo } from '@/routes/discover/-ui/backdrop-video';

import { fetchArtistTopTracks, LIBRARY_V2_QUERY_KEY } from '../-library-v2.api';
import styles from './artist-hero-video.module.css';

/**
 * the artist hero comes alive the way discover's banners do: a music video
 * plays muted behind the photo, with a button for its sound.
 *
 * the video is one of the artist's top five songs, picked at random per
 * visit, through discover's strict official-video lookup (remembered server
 * side for a month). a song with no clean official video falls back to the
 * artist's own top video, and one youtube refuses falls back the same way.
 *
 * it rides discover's video stage, so the same rules hold: nothing plays
 * with video backdrops switched off, with reduced motion, in a hidden tab
 * or off screen, the player is only built once the hero has been seen, and
 * soulsync's own player taking the sound back mutes it.
 */

const SLOT_ID = 'artist-hero-video';
const TOP_PICKS = 5;

function pauseSoulSyncPlayer() {
  const audio = document.getElementById('audio-player') as HTMLAudioElement | null;
  if (audio && !audio.paused) audio.pause();
}

const backdropKey = (artist: string, title: string) =>
  ['discover', 'backdrop-video', artist, title] as const;

export function ArtistHeroVideo({ name, providerId }: { name: string; providerId: string | null }) {
  const [videosOn] = useVideoBackdropsEnabled();
  const [refused, setRefused] = useState<ReadonlySet<string>>(() => new Set());
  const [seed] = useState(() => Math.random());
  const [live, setLive] = useState(false);
  const [candidate, setCandidate] = useState<string | null>(null);
  const slot = useVideoSlot(SLOT_ID, Boolean(candidate));
  const wanted = slot.seen && videosOn && Boolean(name);

  // the top tracks section's own query: shared, never fetched twice
  const top = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'top-tracks', providerId, name],
    queryFn: () => fetchArtistTopTracks({ providerId, name }),
    enabled: wanted,
    staleTime: 5 * 60_000,
  });
  const names = (top.data?.tracks ?? [])
    .slice(0, TOP_PICKS)
    .map((t) => t.name)
    .filter(Boolean);
  const title = names.length ? names[Math.floor(seed * names.length)] : null;
  const topSettled = top.isFetched || top.isError;

  const bySong = useQuery({
    queryKey: backdropKey(name, title ?? ''),
    queryFn: () => fetchBackdropVideo(name, title),
    enabled: wanted && Boolean(title),
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 60 * 60 * 1000,
    retry: false,
  });
  const songVideo = bySong.data?.video_id ?? null;
  const songUsable = songVideo && !refused.has(songVideo) ? songVideo : null;
  // the artist's own top video, only when the song gave nothing to play
  const needArtist =
    wanted && topSettled && (!title || ((bySong.isFetched || bySong.isError) && !songUsable));
  const byArtist = useQuery({
    queryKey: backdropKey(name, ''),
    queryFn: () => fetchBackdropVideo(name),
    enabled: needArtist,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 60 * 60 * 1000,
    retry: false,
  });
  const artistVideo = byArtist.data?.video_id ?? null;
  const next = songUsable ?? (artistVideo && !refused.has(artistVideo) ? artistVideo : null);
  if (next !== candidate) setCandidate(next);

  const onUnplayable = useCallback(
    (id: string) => setRefused((prev) => (prev.has(id) ? prev : new Set(prev).add(id))),
    [],
  );
  const toggleSound = () => {
    if (!slot.soundOn) pauseSoulSyncPlayer();
    slot.setSound(!slot.soundOn);
  };

  return (
    <>
      <div
        ref={slot.ref}
        className={styles.layer}
        data-live={live && slot.playing}
        aria-hidden="true"
      >
        {candidate ? (
          <BackdropVideo
            videoId={candidate}
            playing={slot.playing}
            muted={!slot.soundOn}
            onUnplayable={onUnplayable}
            onLiveChange={setLive}
          />
        ) : null}
      </div>
      {live && slot.playing ? (
        <button
          type="button"
          className={`${styles.sound}${slot.soundOn ? ` ${styles.soundOn}` : ''}`}
          aria-label={slot.soundOn ? 'Mute this video' : 'Play this video with sound'}
          aria-pressed={slot.soundOn}
          title={slot.soundOn ? 'Mute' : 'Tap for sound'}
          onClick={toggleSound}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 9v6h4l5 4V5L8 9H4z" fill="currentColor" />
            {slot.soundOn ? (
              <path
                d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            ) : (
              <path
                d="M16.5 9.5l5 5M21.5 9.5l-5 5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            )}
          </svg>
          <span>{slot.soundOn ? 'Sound on' : 'Tap for sound'}</span>
        </button>
      ) : null}
    </>
  );
}
