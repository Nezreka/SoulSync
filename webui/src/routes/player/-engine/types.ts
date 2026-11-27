/**
 * what the player plays. a PlayItem is everything a play button knows about
 * a thing; the engine asks the server how to play it (playable) when it is
 * its turn, so a queue of a hundred tracks costs nothing up front.
 */

export type ItemType =
  | 'track'
  | 'movie'
  | 'episode'
  | 'youtube'
  | 'home'
  | 'audiobook'
  | 'podcast_episode'
  | 'commercial';

export type MediaKind = 'audio' | 'video';

export interface PlayItem {
  type: ItemType;
  /** the id the server knows it by (library id, youtube video id, asin, episode guid) */
  id: string;
  title: string;
  subtitle?: string;
  /** square or poster art for the transport bar and lock screen */
  artwork?: string | null;
  /** a wide still for video and the full screen view */
  backdrop?: string | null;
  /** its page in the player */
  href?: string;
  /** seconds, when the page already knows */
  duration?: number | null;
  /** a video's file version (media file id) */
  part?: string;
  /** podcast episodes: the enclosure url (streamed through the podcast audio proxy) */
  url?: string;
  /** a youtube video that isn't downloaded plays in youtube's own embed */
  embed?: boolean;
  /** who/what it belongs to, saved with progress (a show, an album, a podcast) */
  parentId?: string;
  parentTitle?: string;
}

export const MEDIA_KIND: Record<ItemType, MediaKind> = {
  track: 'audio',
  audiobook: 'audio',
  podcast_episode: 'audio',
  movie: 'video',
  episode: 'video',
  youtube: 'video',
  home: 'video',
  commercial: 'video',
};

/** long-form things: speed, chapters, skip buttons, a sleep timer, and saved progress */
export const LONG_FORM: ReadonlySet<ItemType> = new Set([
  'audiobook',
  'podcast_episode',
  'movie',
  'episode',
  'youtube',
]);

/** a part of an item served as its own file (an audiobook's files, back to back) */
export interface Part {
  index: number;
  start: number;
  duration: number;
  mime?: string;
}

export interface Chapter {
  start: number;
  /** where it ends (the next one's start); none for a book's or the last */
  end?: number | null;
  title: string;
  /** its picture (a video's, made in the background), once there is one */
  thumb?: string | null;
}

export type RepeatMode = 'off' | 'all' | 'one';
