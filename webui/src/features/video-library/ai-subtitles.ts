/**
 * ai subtitles for one movie or episode: ask the server to make them
 * (/api/video/subtitles/ai/jobs, whisper on this server) and follow the job:
 * place in line, progress, about how long, why it failed. the player's
 * subtitle menu, the find subtitles panel and the title page use it.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { apiClient, refusalText } from '@/app/api-client';

export interface AiJob {
  id: number;
  video_kind: string;
  video_id: number;
  language: string;
  source: string;
  state: string;
  progress: number;
  stage?: string | null;
  eta_seconds?: number | null;
  error?: string | null;
  position?: number | null;
  confidence?: number | null;
  verdict?: string | null;
}

interface JobsAnswer {
  jobs: AiJob[];
  available: boolean;
  why: string | null;
  watching?: boolean;
}

export const ACTIVE = ['queued', 'running', 'paused'];

function ordinal(n: number): string {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

/** a job in plain words: a label and a detail line */
export function jobWords(job: AiJob, watching = false): { label: string; detail: string } {
  const pct = Math.round((job.progress || 0) * 100);
  switch (job.state) {
    case 'running': {
      const eta = job.eta_seconds
        ? job.eta_seconds < 90
          ? 'under 2 min left'
          : `about ${Math.round(job.eta_seconds / 60)} min left`
        : job.stage || 'starting';
      return { label: `Making subtitles with AI… ${pct}%`, detail: eta };
    }
    case 'paused':
      return { label: 'AI subtitles are waiting', detail: 'Someone else is watching right now' };
    case 'queued':
      return {
        label: 'AI subtitles are in line',
        detail:
          job.position && job.position > 1
            ? `${ordinal(job.position)} in line`
            : watching && job.source !== 'on_demand'
              ? 'Waiting while someone watches'
              : 'Starting soon',
      };
    case 'done':
      return {
        label: 'AI subtitles are ready',
        detail:
          job.verdict === 'low'
            ? 'Low confidence: some lines may be wrong'
            : 'Replaced on their own when real ones turn up',
      };
    case 'no_match':
      return {
        label: "AI couldn't make these",
        detail: job.error || 'The audio is another language',
      };
    case 'discarded':
      return {
        label: 'AI subtitles were thrown away',
        detail: job.error || "Whisper wasn't sure enough of what it heard",
      };
    case 'cancelled':
      return { label: 'AI subtitles stopped', detail: 'Make them again any time' };
    default:
      return { label: "AI subtitles didn't work", detail: job.error || 'Try again later' };
  }
}

/**
 * the newest job for this title and language, and a way to ask for one.
 * polls every 3 s while a job waits or runs; ``onReady`` runs once when one
 * finishes with a file (the player switches to it).
 */
export function useAiSubtitles(
  kind: string | null,
  id: string | number | null | undefined,
  lang: string,
  origin: 'player' | 'detail' | 'library',
  onReady?: (lang: string) => void,
) {
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const key = ['video', 'ai-subtitles', kind, String(id ?? ''), lang];
  const jobs = useQuery({
    queryKey: key,
    enabled: !!kind && id != null && id !== '',
    retry: false,
    queryFn: () =>
      apiClient
        .get('video/subtitles/ai/jobs', { searchParams: { kind: kind!, item_id: String(id) } })
        .json<JobsAnswer>(),
    refetchInterval: (q) => {
      const d = q.state.data as JobsAnswer | undefined;
      return d?.jobs.some((j) => ACTIVE.includes(j.state) && j.language === lang) ? 3000 : false;
    },
  });
  const job = (jobs.data?.jobs ?? []).find((j) => j.language === lang) ?? null;
  const seen = useRef<{ id: number; state: string } | null>(null);
  useEffect(() => {
    if (!job) return;
    const before = seen.current;
    seen.current = { id: job.id, state: job.state };
    if (before && before.id === job.id && ACTIVE.includes(before.state) && job.state === 'done')
      onReady?.(lang);
  }, [job, lang, onReady]);

  const generate = async () => {
    if (!kind || id == null) return;
    setAsking(true);
    setError(null);
    try {
      await apiClient
        .post('video/subtitles/ai/jobs', {
          json: { kind, item_id: Number(id), lang, origin },
        })
        .json();
      seen.current = null;
      await client.invalidateQueries({ queryKey: key });
    } catch (e) {
      const res = (e as { response?: Response }).response;
      setError(
        res?.status === 403
          ? "Your profile can't make subtitles. Ask an admin."
          : await refusalText(e, "Couldn't ask for AI subtitles."),
      );
    } finally {
      setAsking(false);
    }
  };

  const cancel = async () => {
    if (!job) return;
    try {
      await apiClient.post(`video/subtitles/ai/jobs/${job.id}/cancel`).json();
    } catch (e) {
      setError(await refusalText(e, "Couldn't stop it."));
    }
    await client.invalidateQueries({ queryKey: key });
  };

  return {
    job,
    active: !!job && ACTIVE.includes(job.state),
    available: jobs.data?.available ?? true,
    why: jobs.data?.why ?? null,
    watching: !!jobs.data?.watching,
    error,
    asking,
    generate,
    cancel,
  };
}
