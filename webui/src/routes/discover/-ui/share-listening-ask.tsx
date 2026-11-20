import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

/**
 * The one-time ask on Made For You: blends mix your plays with someone
 * else's, so they only happen when both of you say yes. Asked once; either
 * answer stops the asking, and My Account has the switch for later.
 */

interface Privacy {
  success?: boolean;
  share_listening?: boolean;
  ask?: boolean;
}

export const SHARE_LISTENING_KEY = ['me', 'listening-privacy'] as const;

async function fetchPrivacy(): Promise<Privacy | null> {
  try {
    const res = await fetch('/api/profiles/me/listening-privacy');
    if (!res.ok) return null;
    return (await res.json()) as Privacy;
  } catch {
    return null;
  }
}

function say(message: string, kind: 'success' | 'error') {
  window.showToast?.(message, kind);
}

export function ShareListeningAsk() {
  const queryClient = useQueryClient();
  const privacy = useQuery({
    queryKey: SHARE_LISTENING_KEY,
    queryFn: fetchPrivacy,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
  const [busy, setBusy] = useState(false);
  const [gone, setGone] = useState(false);
  if (gone || !privacy.data?.ask) return null;

  const answer = async (yes: boolean) => {
    setBusy(true);
    try {
      const res = await fetch('/api/profiles/me/listening-privacy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(yes ? { share_listening: true } : { asked: true }),
      });
      const data = (await res.json().catch(() => null)) as Privacy | null;
      if (!res.ok || !data || data.success === false) throw new Error('save failed');
      setGone(true);
      queryClient.setQueryData(SHARE_LISTENING_KEY, data);
      if (yes) {
        say('Sharing your listening. Blends show up once someone else says yes too.', 'success');
        void queryClient.invalidateQueries({ queryKey: ['discover', 'for-you'] });
      }
    } catch {
      say("Couldn't save that. Try again.", 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="discover-share-ask" role="region" aria-label="Share your listening">
      <div className="discover-share-ask-text">
        <strong>Make blends with people on this server?</strong> A blend is a mix of what you and
        someone else play. It's only made when you both say yes, and you can turn it off any time in
        My Account.
      </div>
      <div className="discover-share-ask-actions">
        <button
          type="button"
          className="discover-build-mix-btn"
          disabled={busy}
          onClick={() => void answer(true)}
        >
          Share my listening
        </button>
        <button
          type="button"
          className="discover-share-ask-later"
          disabled={busy}
          onClick={() => void answer(false)}
        >
          Not now
        </button>
      </div>
    </div>
  );
}
