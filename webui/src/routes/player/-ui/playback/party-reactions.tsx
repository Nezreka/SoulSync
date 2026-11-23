/**
 * Live full-screen Watch Party floating emoji reactions overlay:
 * - Polls the active Watch Party room every 2s while watching in fullscreen
 * - Elegantly floats incoming reactions from all guests (Desktop + Android)
 *   up the right edge of the video screen with a gentle sway & sender pill
 * - Provides a sleek frosted reaction dock in the HUD for 1-click reactions
 */

import { useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { watchPartyApi } from '../../-player.api';

const REACTION_EMOJIS = ['🍿', '🔥', '😂', '😮', '👏', '❤️'] as const;

let currentPartyId: string | null = null;
const partyListeners = new Set<() => void>();

export function setActiveWatchPartyId(id: string | null) {
  if (currentPartyId === id) return;
  currentPartyId = id;
  for (const fn of partyListeners) fn();
}

export function useActiveWatchPartyId(): string | null {
  return useSyncExternalStore(
    (fn) => {
      partyListeners.add(fn);
      return () => {
        partyListeners.delete(fn);
      };
    },
    () => currentPartyId,
    () => null,
  );
}

interface FloatingBubble {
  uid: string;
  emoji: string;
  sender: string;
  rightPx: number;
  swayPx: number;
}

export function PartyReactionsOverlay({ hudVisible }: { hudVisible: boolean }) {
  const partyId = useActiveWatchPartyId();
  const [bubbles, setBubbles] = useState<FloatingBubble[]>([]);
  const [muted, setMuted] = useState(false);
  const seenIdsRef = useRef<Set<number>>(new Set());
  const initializedRef = useRef<string | null>(null);

  const spawnBubble = (uid: string, emoji: string, sender: string, seed: number) => {
    const rightPx = 22 + ((seed * 23) % 72);
    const swayPx = ((seed % 2 === 0 ? 1 : -1) * (10 + (seed % 14)));
    const next: FloatingBubble = { uid, emoji, sender, rightPx, swayPx };
    setBubbles((prev) => [...prev.slice(-14), next]);
    window.setTimeout(() => {
      setBubbles((prev) => prev.filter((b) => b.uid !== uid));
    }, 3400);
  };

  useEffect(() => {
    if (!partyId) {
      seenIdsRef.current.clear();
      initializedRef.current = null;
      return;
    }
    let alive = true;
    const poll = async () => {
      try {
        const party = await watchPartyApi.get(partyId);
        if (!alive || !party) return;
        const msgs = party.messages ?? [];
        // On first poll for this party, mark existing history as seen so old reactions don't flood
        if (initializedRef.current !== partyId) {
          initializedRef.current = partyId;
          seenIdsRef.current = new Set(msgs.map((m) => m.id));
          return;
        }
        for (const m of msgs) {
          if (seenIdsRef.current.has(m.id)) continue;
          seenIdsRef.current.add(m.id);
          const emoji = m.body.trim();
          const isReaction = m.kind === 'reaction' || (REACTION_EMOJIS as readonly string[]).includes(emoji);
          if (isReaction && !muted) {
            spawnBubble(`srv-${m.id}`, emoji, m.profile?.name ?? '', m.id);
          }
        }
      } catch {
        /* ignore transient poll failures */
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [partyId, muted]);

  if (!partyId) return null;

  const sendReaction = async (emoji: string) => {
    spawnBubble(`local-${Date.now()}-${Math.random()}`, emoji, 'You', Date.now());
    try {
      const res = await watchPartyApi.chat(partyId, { kind: 'reaction', body: emoji });
      if (res?.id) seenIdsRef.current.add(res.id);
    } catch {
      /* ignore */
    }
  };

  return (
    <>
      <style>{`
        @keyframes ssPartyFloatUp {
          0% {
            opacity: 0;
            transform: translate3d(0, 18px, 0) scale(0.65);
          }
          14% {
            opacity: 1;
            transform: translate3d(var(--ss-sway, 10px), -26px, 0) scale(1.06);
          }
          55% {
            opacity: 0.96;
            transform: translate3d(calc(var(--ss-sway, 10px) * -0.7), -180px, 0) scale(1);
          }
          100% {
            opacity: 0;
            transform: translate3d(var(--ss-sway, 10px), -340px, 0) scale(0.92);
          }
        }
      `}</style>

      {/* Elegant floating emoji bubbles along the right edge (clear of subtitles & center action) */}
      {!muted && (
        <div
          aria-hidden="true"
          style={{
            position: 'absolute',
            right: 0,
            bottom: 92,
            width: 160,
            height: 380,
            pointerEvents: 'none',
            overflow: 'hidden',
            zIndex: 25,
          }}
        >
          {bubbles.map((b) => (
            <div
              key={b.uid}
              style={{
                position: 'absolute',
                right: b.rightPx,
                bottom: 8,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: 3,
                ['--ss-sway' as string]: `${b.swayPx}px`,
                animation: 'ssPartyFloatUp 3.2s cubic-bezier(0.22, 1, 0.36, 1) forwards',
              }}
            >
              <span
                style={{
                  fontSize: 30,
                  lineHeight: 1,
                  filter: 'drop-shadow(0 6px 14px rgba(0,0,0,0.65))',
                }}
              >
                {b.emoji}
              </span>
              {b.sender && (
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    color: 'rgba(255,255,255,0.92)',
                    background: 'rgba(10,12,20,0.65)',
                    border: '1px solid rgba(255,255,255,0.16)',
                    backdropFilter: 'blur(8px)',
                    padding: '1px 7px',
                    borderRadius: 999,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {b.sender}
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Sleek frosted Reaction Bar inside the Cinema HUD */}
      {hudVisible && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: 'absolute',
            right: 24,
            bottom: 96,
            zIndex: 30,
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            padding: '5px 8px',
            borderRadius: 999,
            background: 'rgba(12, 14, 22, 0.72)',
            border: '1px solid rgba(255,255,255,0.14)',
            backdropFilter: 'blur(14px)',
            boxShadow: '0 10px 30px rgba(0,0,0,0.45)',
          }}
        >
          {REACTION_EMOJIS.map((emoji) => (
            <button
              key={emoji}
              type="button"
              onClick={() => void sendReaction(emoji)}
              title={`React ${emoji}`}
              style={{
                width: 34,
                height: 34,
                borderRadius: '50%',
                border: 'none',
                background: 'transparent',
                fontSize: 19,
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'transform 120ms ease, background 120ms ease',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = 'scale(1.25)';
                e.currentTarget.style.background = 'rgba(255,255,255,0.12)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'scale(1)';
                e.currentTarget.style.background = 'transparent';
              }}
            >
              {emoji}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setMuted((m) => !m)}
            title={muted ? 'Show floating reactions' : 'Hide floating reactions'}
            style={{
              marginLeft: 2,
              padding: '4px 9px',
              borderRadius: 999,
              border: '1px solid rgba(255,255,255,0.14)',
              background: muted ? 'rgba(244,63,94,0.22)' : 'rgba(255,255,255,0.08)',
              color: '#fff',
              fontSize: 10.5,
              fontWeight: 700,
              cursor: 'pointer',
            }}
          >
            {muted ? 'Off' : 'Live'}
          </button>
        </div>
      )}
    </>
  );
}
