export interface PodcastEpisodeItem {
  guid: string;
  title: string;
  enclosure_url: string;
  enclosure_type: string;
  enclosure_length?: number | null;
  pub_date?: string | null;
  duration_seconds?: number | null;
  description: string;
  show_notes: string;
  season?: number | null;
  episode_number?: number | null;
  episode_type: string;
  artwork_url?: string | null;
  chapter_url?: string | null;
  transcript_url?: string | null;
}

export interface PodcastShowSummary {
  title: string;
  author: string;
  description: string;
  artwork_url?: string | null;
  feed_url?: string | null;
  itunes_id?: number | null;
  website?: string | null;
  language?: string | null;
  explicit: boolean;
  categories: string[];
  episode_count?: number | null;
}

export interface PodcastShowDetail extends PodcastShowSummary {
  episodes: PodcastEpisodeItem[];
}

export interface PodcastDownloadItem {
  download_id: string;
  title: string;
  show_title: string;
  artwork_url?: string;
  enclosure_url: string;
  duration_seconds?: number | null;
  status: 'queued' | 'downloading' | 'completed' | 'error';
  progress_bytes: number;
  total_bytes: number;
  percent: number;
  file_path?: string | null;
  error?: string | null;
  started_at: number;
  completed_at?: number | null;
}

export interface ActivePlaybackState {
  episode: PodcastEpisodeItem;
  showTitle: string;
  showArtwork?: string | null;
  isPlaying: boolean;
  currentTime: number;
  duration: number;
  playbackRate: number;
}
