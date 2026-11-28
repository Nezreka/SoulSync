/**
 * Live TV player overlay: tunes a shared HLS channel via /api/live/tune,
 * plays the sliding time-shift HLS stream, sends periodic heartbeats,
 * offers Jump to Live and 1-click DVR Record, and cleanly releases the tuner
 * via /api/live/leave when closed.
 */

import { useEffect, useRef, useState } from 'react';

import { appURL } from '@/platform/url-base';

import {
  heartbeatLiveChannel,
  leaveLiveChannel,
  scheduleDvrRecording,
  tuneLiveChannel,
  type LiveChannel,
} from '../-lib/live';

export function LivePlayerModal({
  channel,
  programTitle,
  programStartTs,
  programEndTs,
  onClose,
}: {
  channel: LiveChannel;
  programTitle?: string;
  programStartTs?: number;
  programEndTs?: number;
  onClose: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [status, setStatus] = useState<'tuning' | 'playing' | 'error'>('tuning');
  const [error, setError] = useState<string | null>(null);
  const [viewers, setViewers] = useState(1);
  const [bufferS, setBufferS] = useState(0);
  const [recState, setRecState] = useState<'idle' | 'saving' | 'done' | 'error'>('idle');

  useEffect(() => {
    let cancelled = false;
    let hbTimer: number | undefined;
    let hlsInstance: { destroy: () => void } | null = null;

    async function start() {
      setStatus('tuning');
      setError(null);
      try {
        const res = await tuneLiveChannel(channel.id);
        if (cancelled) return;
        if (!res.success || !res.session?.playlist_url) {
          setStatus('error');
          setError(res.message || res.error || 'Could not tune channel.');
          return;
        }
        setViewers(res.session.viewers ?? 1);
        const src = appURL(res.session.playlist_url);
        const video = videoRef.current;
        if (video) {
          const HlsCtor = (
            window as Window & {
              Hls?: {
                isSupported: () => boolean;
                new (opts?: Record<string, unknown>): {
                  loadSource: (u: string) => void;
                  attachMedia: (el: HTMLVideoElement) => void;
                  destroy: () => void;
                };
              };
            }
          ).Hls;
          if (video.canPlayType('application/vnd.apple.mpegurl')) {
            video.src = src;
            void video.play().catch(() => {});
          } else if (HlsCtor?.isSupported()) {
            const hls = new HlsCtor({ liveSyncDurationCount: 3 });
            hls.loadSource(src);
            hls.attachMedia(video);
            hlsInstance = hls;
            void video.play().catch(() => {});
          } else {
            video.src = src;
            void video.play().catch(() => {});
          }
        }
        setStatus('playing');
        hbTimer = window.setInterval(() => {
          void heartbeatLiveChannel(channel.id)
            .then((hb) => {
              if (!cancelled && hb.session) {
                if (typeof hb.session.viewers === 'number') setViewers(hb.session.viewers);
                if (typeof hb.session.timeshift_seconds === 'number') {
                  setBufferS(hb.session.timeshift_seconds);
                }
              }
            })
            .catch(() => {});
        }, 15_000);
      } catch (err) {
        if (!cancelled) {
          setStatus('error');
          setError(err instanceof Error ? err.message : 'Could not tune channel.');
        }
      }
    }

    void start();
    return () => {
      cancelled = true;
      if (hbTimer) window.clearInterval(hbTimer);
      hlsInstance?.destroy();
      void leaveLiveChannel(channel.id);
    };
  }, [channel.id]);

  const jumpToLive = () => {
    const v = videoRef.current;
    if (v && v.seekable && v.seekable.length > 0) {
      v.currentTime = v.seekable.end(v.seekable.length - 1);
      void v.play().catch(() => {});
    }
  };

  const recordNow = async () => {
    setRecState('saving');
    const nowS = Math.floor(Date.now() / 1000);
    const res = await scheduleDvrRecording({
      channel_id: channel.id,
      title: programTitle || channel.display_name,
      start_ts: programStartTs ?? nowS,
      end_ts: programEndTs ?? nowS + 3600,
    }).catch(() => ({ success: false }));
    setRecState(res.success ? 'done' : 'error');
  };

  return (
    <div
      role="dialog"
      aria-label={`Live TV: ${channel.display_name}`}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 90,
        background: 'rgba(6, 9, 14, 0.92)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 20,
      }}
    >
      <div style={{ width: '100%', maxWidth: 1080, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span
            style={{
              background: '#dc2626',
              color: '#fff',
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.06em',
              padding: '3px 8px',
              borderRadius: 999,
            }}
          >
            LIVE
          </span>
          <strong style={{ fontSize: 16 }}>
            {channel.guide_number ? `${channel.guide_number} · ` : ''}
            {channel.display_name}
          </strong>
          {programTitle ? <span style={{ opacity: 0.78, fontSize: 14 }}>{programTitle}</span> : null}
          <span style={{ marginLeft: 'auto', fontSize: 12, opacity: 0.7 }}>
            {viewers} {viewers === 1 ? 'viewer' : 'viewers'}
            {bufferS >= 60 ? ` · ${Math.floor(bufferS / 60)}m buffer` : ''}
          </span>
          <button type="button" className="dlc-btn" onClick={jumpToLive}>
            Jump to Live
          </button>
          <button
            type="button"
            className="dlc-btn"
            disabled={recState === 'saving' || recState === 'done'}
            onClick={() => void recordNow()}
          >
            {recState === 'done' ? 'Scheduled' : recState === 'saving' ? 'Scheduling…' : 'Record'}
          </button>
          <button type="button" className="dlc-btn" onClick={onClose}>
            Close
          </button>
        </div>

        <div
          style={{
            position: 'relative',
            width: '100%',
            aspectRatio: '16 / 9',
            background: '#000',
            borderRadius: 12,
            overflow: 'hidden',
          }}
        >
          <video
            ref={videoRef}
            controls
            playsInline
            style={{ width: '100%', height: '100%', objectFit: 'contain' }}
          />
          {status === 'tuning' ? (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                background: 'rgba(0,0,0,0.65)',
                color: '#fff',
                fontSize: 14,
              }}
            >
              Tuning {channel.display_name}…
            </div>
          ) : null}
          {status === 'error' ? (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                background: 'rgba(0,0,0,0.8)',
                color: '#fca5a5',
                fontSize: 14,
                padding: 20,
                textAlign: 'center',
              }}
            >
              {error}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
