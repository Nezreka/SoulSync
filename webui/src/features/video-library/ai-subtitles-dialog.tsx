/**
 * make subtitles with ai for one movie or episode, from its page: pick the
 * language, ask, and watch it go (place in line, progress, about how long).
 * the server makes them from the audio (whisper) and names them
 * Movie.ai.en.srt; a real subtitle replaces them when one turns up.
 */

import { useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';

import { jobWords, useAiSubtitles } from './ai-subtitles';
import s from './video-library.module.css';

export const AI_LANGS = [
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

const NAMES =
  typeof Intl !== 'undefined' && 'DisplayNames' in Intl
    ? new Intl.DisplayNames(undefined, { type: 'language' })
    : null;

function langName(code: string): string {
  try {
    return NAMES?.of(code) ?? code;
  } catch {
    return code;
  }
}

function firstLang(): string {
  try {
    const v = localStorage.getItem('ss-player:subLang');
    if (v && v !== 'off') return v.slice(0, 2);
  } catch {
    /* no storage */
  }
  return (navigator.language || 'en').slice(0, 2).toLowerCase();
}

export function AiSubtitlesDialog({
  kind,
  id,
  title,
  onClose,
  onReady,
}: {
  kind: 'movie' | 'episode';
  id: number;
  title: string;
  onClose: () => void;
  onReady?: () => void;
}) {
  const [lang, setLang] = useState(firstLang);
  const ai = useAiSubtitles(kind, id, lang, 'detail', () => onReady?.());
  const words = ai.job ? jobWords(ai.job, ai.watching) : null;
  const pct = ai.job ? Math.round((ai.job.progress || 0) * 100) : 0;
  const options = AI_LANGS.includes(lang) ? AI_LANGS : [lang, ...AI_LANGS];
  return (
    <DialogFrame
      open
      onOpenChange={(open) => !open && onClose()}
      aria-label="Make subtitles with AI"
      className={s.dialog}
    >
      <DialogHeader title="Make subtitles with AI">
        <span className={s.muted}>{title}</span>
      </DialogHeader>
      <DialogBody>
        <label className={s.field}>
          <span>Language</span>
          <select
            className={s.select}
            value={lang}
            aria-label="Subtitle language"
            onChange={(e) => setLang(e.target.value)}
          >
            {options.map((code) => (
              <option key={code} value={code}>
                {langName(code)}
              </option>
            ))}
          </select>
        </label>
        {words ? (
          <div className={s.aiStatus} data-state={ai.job?.state} role="status">
            <b>{words.label}</b>
            <span className={s.muted}>{words.detail}</span>
            {ai.active ? (
              <div className={s.aiBar} aria-hidden="true">
                <div style={{ width: `${Math.max(2, pct)}%` }} />
              </div>
            ) : null}
          </div>
        ) : null}
        {ai.error || (!ai.available && ai.why) ? (
          <p className={s.note} role="alert">
            {ai.error || ai.why}
          </p>
        ) : null}
        <p className={s.note}>
          Your server listens to the audio and writes the subtitles: nothing leaves it. Spoken lines
          only, no sound descriptions. When the audio is another language, English ones are
          translated from it. They are saved as {'“'}.ai.{lang}.srt{'”'} so Plex, Jellyfin, Emby and
          Kodi still read the language, and a real subtitle replaces them when one turns up.
        </p>
      </DialogBody>
      <DialogFooter>
        <button type="button" className={s.quietBtn} onClick={onClose}>
          Close
        </button>
        {ai.active ? (
          <button type="button" className={s.quietBtn} onClick={() => void ai.cancel()}>
            Stop
          </button>
        ) : (
          <button
            type="button"
            className={s.btnPrimary}
            disabled={ai.asking || !ai.available}
            onClick={() => void ai.generate()}
          >
            {ai.asking ? 'Asking…' : ai.job?.state === 'done' ? 'Make them again' : 'Generate'}
          </button>
        )}
      </DialogFooter>
    </DialogFrame>
  );
}
