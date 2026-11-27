/**
 * a pasted youtube link on the files search (#2322). the box shows exactly
 * that video, or the playlist's videos, instead of searching. the server
 * checks the link again (core/search/youtube_link.py), this side only decides
 * whether to ask it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { apiClient, readJson } from '@/app/api-client';

import type { BasicTrack } from './-basic.types';

import { postDownload } from './-basic.api';

export type YoutubeLink = { kind: 'video'; id: string } | { kind: 'playlist'; id: string };

const VIDEO_HOSTS = new Set([
  'youtube.com',
  'www.youtube.com',
  'm.youtube.com',
  'music.youtube.com',
]);
const SHORT_HOSTS = new Set(['youtu.be', 'www.youtu.be']);
const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
const LIST_ID = /^[A-Za-z0-9_-]{2,64}$/;
const ID_PATHS = new Set(['shorts', 'live', 'embed', 'v']);

/**
 * a youtube video or playlist link, or null for anything else. a watch link
 * inside a list is the video, only a /playlist link is a playlist. same rules
 * as parse_youtube_url on the server.
 */
export function parseYoutubeUrl(value: unknown): YoutubeLink | null {
  if (typeof value !== 'string') return null;
  const raw = value.trim();
  if (!raw || raw.length > 2048 || /\s/.test(raw)) return null;
  let url: URL;
  try {
    url = new URL(raw.includes('://') ? raw : `https://${raw}`);
  } catch {
    return null;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
  if (url.username || url.password) return null;
  if (url.port && url.port !== '80' && url.port !== '443') return null;
  const host = url.hostname.toLowerCase().replace(/\.$/, '');
  const segments = url.pathname.split('/').filter(Boolean);
  const video = (id: string | null | undefined): YoutubeLink | null =>
    id && VIDEO_ID.test(id) ? { kind: 'video', id } : null;

  if (SHORT_HOSTS.has(host)) return segments.length === 1 ? video(segments[0]) : null;
  if (!VIDEO_HOSTS.has(host)) return null;
  if (segments.length === 1 && segments[0] === 'watch') return video(url.searchParams.get('v'));
  if (segments.length === 2 && ID_PATHS.has(segments[0] ?? '')) return video(segments[1]);
  if (segments.length === 1 && segments[0] === 'playlist') {
    const list = url.searchParams.get('list');
    return list && LIST_ID.test(list) ? { kind: 'playlist', id: list } : null;
  }
  return null;
}

export function isYoutubeUrl(value: unknown): boolean {
  return parseYoutubeUrl(value) !== null;
}

/** one video, a files-search track row plus what the panel shows */
export interface YoutubeLinkRow extends BasicTrack {
  thumbnail?: string | null;
  video_id?: string | null;
  channel?: string;
  video_title?: string;
}

/** POST /api/search/youtube-link */
export interface YoutubeLinkResponse {
  kind: 'video' | 'playlist';
  id: string;
  url: string;
  title: string;
  channel: string;
  thumbnail: string | null;
  results: YoutubeLinkRow[];
  count: number;
  /** the playlist had more than the server shows */
  truncated: boolean;
}

/** metadata only, nothing downloads. a refusal throws with the server's words */
export async function resolveYoutubeLink(
  url: string,
  signal?: AbortSignal,
): Promise<YoutubeLinkResponse> {
  return readJson<YoutubeLinkResponse>(
    apiClient.post('search/youtube-link', { json: { url: url.trim() }, timeout: false, signal }),
  );
}

/**
 * queue rows as-is through /api/download, one after another, with one toast
 * at the end instead of one per video. a blocklisted artist is skipped here,
 * the single-row download asks about it. returns how many started.
 */
export async function downloadYoutubeRows(rows: YoutubeLinkRow[]): Promise<number> {
  let started = 0;
  let skipped = 0;
  let failed = 0;
  for (const row of rows) {
    try {
      const data = await postDownload({ ...row, result_type: 'track' });
      if (data.blocked) skipped += 1;
      else if (data.success) started += 1;
      else failed += 1;
    } catch {
      failed += 1;
    }
  }
  const parts = [`Started ${started} of ${rows.length}`];
  if (skipped) parts.push(`${skipped} blocklisted`);
  if (failed) parts.push(`${failed} failed`);
  window.showToast?.(parts.join(', '), started ? 'success' : 'error');
  return started;
}

export interface YoutubeLinkState {
  status: 'idle' | 'loading' | 'ready' | 'error';
  url: string;
  data: YoutubeLinkResponse | null;
  error: string;
}

const IDLE: YoutubeLinkState = { status: 'idle', url: '', data: null, error: '' };

/** the panel's state: one lookup at a time, a new one or clear() drops the old */
export function useYoutubeLink() {
  const [state, setState] = useState<YoutubeLinkState>(IDLE);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const resolve = useCallback(async (url: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ status: 'loading', url, data: null, error: '' });
    try {
      const data = await resolveYoutubeLink(url, controller.signal);
      if (controller.signal.aborted) return;
      setState({ status: 'ready', url, data, error: '' });
    } catch (error) {
      if (controller.signal.aborted) return;
      setState({
        status: 'error',
        url,
        data: null,
        error: (error as Error)?.message || "Couldn't read that YouTube link.",
      });
    }
  }, []);

  const clear = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState(IDLE);
  }, []);

  return { state, resolve, clear };
}
