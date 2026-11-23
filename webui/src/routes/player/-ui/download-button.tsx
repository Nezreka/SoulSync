import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { triggerBrowserDownload, type DownloadItemType } from '../-lib/downloads';
import s from './player.module.css';

export function DownloadMediaButton({
  itemType,
  itemId,
  title,
  subtitle,
  artwork,
  directUrl,
  compact = false,
  label = 'Download',
}: {
  itemType: DownloadItemType;
  itemId: string;
  title: string;
  subtitle?: string | null;
  artwork?: string | null;
  directUrl?: string;
  compact?: boolean;
  label?: string;
}) {
  const qc = useQueryClient();
  const [state, setState] = useState<'idle' | 'busy' | 'done' | 'error'>('idle');
  const [errMsg, setErrMsg] = useState<string | null>(null);

  const handleClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (state === 'busy') return;
    setState('busy');
    setErrMsg(null);
    const res = await triggerBrowserDownload({
      itemType,
      itemId,
      title,
      subtitle,
      artwork,
      directUrl,
    });
    if (res.ok) {
      setState('done');
      void qc.invalidateQueries({ queryKey: ['player', 'downloads'] });
      window.setTimeout(() => setState('idle'), 3000);
    } else {
      setState('error');
      setErrMsg(res.error || 'Download blocked');
    }
  };

  return (
    <button
      type="button"
      className={compact ? undefined : s.btn}
      onClick={handleClick}
      disabled={state === 'busy'}
      title={errMsg || `Download ${title}`}
      aria-label={`Download ${title}`}
      style={
        compact
          ? {
              background: 'transparent',
              border: 'none',
              color: state === 'done' ? '#6ee7b7' : state === 'error' ? '#fda4af' : 'inherit',
              cursor: 'pointer',
              padding: '4px 6px',
              borderRadius: 6,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              opacity: 0.78,
            }
          : undefined
      }
    >
      <svg
        width={compact ? 15 : 16}
        height={compact ? 15 : 16}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        {state === 'done' ? (
          <polyline points="20 6 9 17 4 12" />
        ) : (
          <>
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </>
        )}
      </svg>
      {!compact && (
        <span style={{ marginLeft: 6 }}>
          {state === 'busy'
            ? 'Preparing…'
            : state === 'done'
              ? 'Downloaded'
              : state === 'error'
                ? 'Blocked'
                : label}
        </span>
      )}
    </button>
  );
}
