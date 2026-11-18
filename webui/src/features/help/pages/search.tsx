/**
 * Search and Discover: when a source is the reason results come back thin.
 * /status (web_server.py get_status, cached server side, the same read the
 * dashboard and the sidebar make) carries Spotify's rate limit with the time
 * left, whether the primary metadata source answers, and on Search whether
 * the download client is connected.
 *
 * Nothing here is per profile, so a member reads the same why; the Settings
 * button is the admin's.
 */

import { getMetadataSourceLabel } from '@/routes/dashboard/-dash.services';

import { registerPageHelp, type AnswerContext, type PageAnswer } from './index';
import { Act, Acts, getJson, openSettings, spoken, useLive } from './kit';

export interface SourceStatus {
  metadata_source?: { source?: string; connected?: boolean | null } | null;
  spotify?: {
    rate_limited?: boolean;
    rate_limit?: { remaining_seconds?: number } | null;
  } | null;
  soulseek?: { connected?: boolean | null; source?: string | null } | null;
}

const CLIENTS: Record<string, string> = {
  soulseek: 'Soulseek (slskd)',
  torrent: 'Your torrent client',
  usenet: 'Your usenet client',
};

export function sourceAnswers(
  status: SourceStatus | undefined,
  ctx: AnswerContext,
  where: 'search' | 'discover',
): PageAnswer[] {
  if (!status) return [];
  const out: PageAnswer[] = [];

  const sp = status.spotify;
  if (sp?.rate_limited && sp.rate_limit) {
    const left = Number(sp.rate_limit.remaining_seconds) || 0;
    out.push({
      id: 'spotify-limit',
      title: 'Why is Spotify paused?',
      live: true,
      body: (
        <p>
          Spotify asked SoulSync to slow down, so it's paused
          {left > 0 ? ` for about ${spoken(left)} more` : ' for now'}. It picks up again on its own,
          nothing to do. Your other sources keep working.
        </p>
      ),
    });
  }

  const meta = status.metadata_source;
  if (meta && meta.connected === false && meta.source) {
    const label = getMetadataSourceLabel(meta.source);
    out.push({
      id: 'metadata-down',
      title:
        where === 'search' ? 'Why are searches coming back empty?' : 'Why are some shelves empty?',
      live: true,
      body: (
        <>
          <p>
            {label === 'Unmapped' ? 'Your metadata source' : label} isn't answering right now, and
            it's where SoulSync looks things up, so results that need it come back thin or empty
            until it's back.
          </p>
          {ctx.isAdmin ? (
            <Acts>
              <Act kind="secondary" onClick={() => openSettings('sources')}>
                Open Sources
              </Act>
            </Acts>
          ) : null}
        </>
      ),
    });
  }

  const dl = status.soulseek;
  if (where === 'search' && dl && dl.connected === false && dl.source) {
    const hybrid = dl.source === 'hybrid';
    const name = CLIENTS[dl.source];
    if (hybrid || name) {
      out.push({
        id: 'client-down',
        title: "Why won't downloads start from here?",
        live: true,
        body: (
          <>
            <p>
              {hybrid
                ? "Your download client isn't connected, so downloads that need it can't start until it's back."
                : `${name} isn't connected, so nothing downloads until it's back.`}
            </p>
            {ctx.isAdmin ? (
              <Acts>
                <Act kind="secondary" onClick={() => openSettings('downloads')}>
                  Open Downloads settings
                </Act>
              </Acts>
            ) : null}
          </>
        ),
      });
    }
  }

  if (out.length) out[0] = { ...out[0], open: true };
  return out;
}

function makeUse(where: 'search' | 'discover') {
  return function useSourceAnswers(ctx: AnswerContext): PageAnswer[] {
    const status = useLive(() => getJson<SourceStatus>('/status'), 30_000);
    if (!status.loaded) return [];
    return sourceAnswers(status.data, ctx, where);
  };
}

export const useSearchAnswers = makeUse('search');
export const useDiscoverAnswers = makeUse('discover');

registerPageHelp({ id: 'search', useAnswers: useSearchAnswers });
registerPageHelp({ id: 'discover', useAnswers: useDiscoverAnswers });
