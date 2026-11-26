/**
 * find subtitles while you watch: soulsync's own subtitle providers
 * (/api/video/subtitles/search), best match first, a file-hash match
 * marked. the one picked is saved beside the video, and the player
 * switches to it straight away.
 */

import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { apiClient, refusalText } from '@/app/api-client';

import type { PlayerState } from '../../-engine/engine';

import { languageName } from '../video-facts';
import { CloseIcon } from './icons';
import s from './playback.module.css';
import { getEngine } from './use-player';

interface Candidate {
  candidate_id: string;
  provider: string;
  title: string;
  score: number;
  hash_match: boolean;
  download_count: number;
  language: string;
  hi: boolean;
  forced: boolean;
}

const LANGS = [
  'en',
  'es',
  'fr',
  'de',
  'it',
  'pt',
  'nl',
  'sv',
  'no',
  'da',
  'fi',
  'pl',
  'ru',
  'ja',
  'ko',
  'zh',
  'ar',
  'he',
  'hi',
  'tr',
];

function startLang(): string {
  try {
    const v = localStorage.getItem('ss-player:subLang');
    if (v && v !== 'off') return v.slice(0, 2);
  } catch {
    /* no storage */
  }
  return (navigator.language || 'en').slice(0, 2).toLowerCase();
}

async function errorText(e: unknown, fallback: string): Promise<string> {
  const res = (e as { response?: Response }).response;
  if (res?.status === 403) return "Your profile can't download subtitles. Ask an admin.";
  return refusalText(e, fallback);
}

export function FindSubtitles({ state, onClose }: { state: PlayerState; onClose: () => void }) {
  const item = state.item;
  const kind = item?.type === 'movie' || item?.type === 'episode' ? item.type : null;
  const [lang, setLang] = useState(startLang);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const search = useQuery({
    queryKey: ['player', 'subtitle-search', kind, item?.id, lang],
    enabled: !!kind,
    staleTime: 5 * 60_000,
    retry: false,
    queryFn: () =>
      apiClient
        .get(`video/subtitles/search/${kind}/${item!.id}`, {
          searchParams: { lang },
          timeout: 60_000,
        })
        .json<{ candidates: Candidate[] }>(),
  });

  const download = async (c: Candidate) => {
    setBusy(c.candidate_id);
    setError(null);
    try {
      await apiClient
        .post('video/subtitles/manual-download', {
          json: { kind, item_id: Number(item!.id), lang, candidate_id: c.candidate_id },
          timeout: 60_000,
        })
        .json();
      await getEngine().refreshTracks(lang);
      onClose();
    } catch (e) {
      setError(await errorText(e, "Couldn't get that one. Try another."));
    } finally {
      setBusy(null);
    }
  };

  const options = LANGS.includes(lang) ? LANGS : [lang, ...LANGS];
  const found = search.data?.candidates ?? [];
  return (
    <section className={s.episodes} aria-label="Find subtitles">
      <div className={s.episodesHead}>
        <span className={s.episodesTitle}>Find subtitles</span>
        <button type="button" className={s.ctl} aria-label="Close" onClick={onClose} data-nav>
          <CloseIcon />
        </button>
      </div>
      <label className={s.finderLang}>
        <span>Language</span>
        <select value={lang} onChange={(e) => setLang(e.target.value)}>
          {options.map((code) => (
            <option key={code} value={code}>
              {languageName(code)}
            </option>
          ))}
        </select>
      </label>
      {error ? (
        <p className={s.finderError} role="alert">
          {error}
        </p>
      ) : null}
      {search.isLoading ? <p className={s.episodesNote}>Asking your subtitle providers…</p> : null}
      {search.isError ? (
        <p className={s.episodesNote}>
          The search didn't work. Check the subtitle providers in Settings.
        </p>
      ) : null}
      {search.isSuccess && !found.length ? (
        <p className={s.episodesNote}>Nothing in {languageName(lang)} from your providers.</p>
      ) : null}
      <ol className={s.episodesList}>
        {found.map((c) => (
          <li key={c.candidate_id}>
            <button
              type="button"
              className={s.finderRow}
              disabled={!!busy}
              onClick={() => void download(c)}
              data-nav
            >
              <span className={s.episodeText}>
                <span className={s.episodeName}>{c.title || 'Untitled'}</span>
                <span className={s.episodeMeta}>
                  <span>{c.provider}</span>
                  {c.hash_match ? (
                    <span className={s.episodeSeen}>✓ Made for this file</span>
                  ) : null}
                  {c.hi ? <span>SDH</span> : null}
                  {c.forced ? <span>Forced</span> : null}
                  {c.download_count ? (
                    <span>{c.download_count.toLocaleString()} downloads</span>
                  ) : null}
                </span>
              </span>
              <span className={s.finderScore}>
                {busy === c.candidate_id ? 'Getting…' : `${Math.round(c.score)}`}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}
