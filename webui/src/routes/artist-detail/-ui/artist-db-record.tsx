import { useState } from 'react';

import { RecordInspector } from '@/components/record-inspector/record-inspector';

import type { ArtistInfo } from '../-artist-detail.types';

import { showsDbRecordButton } from '../-artist-detail.db-record';
interface Props {
  artist: ArtistInfo;
  isSourceArtist: boolean;
}

/**
 * The "DB Record" inspector button and its modal.
 *
 * The vanilla appended the button to #artist-hero-section by hand and rebuilt
 * it on every artist; here it is part of the hero, which is why the
 * library-only condition is a render guard rather than a display toggle.
 */
export function ArtistDbRecord({ artist, isSourceArtist }: Props) {
  const [open, setOpen] = useState(false);
  if (!showsDbRecordButton(artist, isSourceArtist)) return null;

  return (
    <>
      <button
        type="button"
        className="artist-db-record-btn"
        id="artist-db-record-btn"
        title="Inspect everything the database knows about this artist"
        onClick={() => setOpen(true)}
      >
        <svg
          viewBox="0 0 24 24"
          width="14"
          height="14"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <ellipse cx="12" cy="5" rx="8" ry="3" />
          <path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
          <path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" />
        </svg>
        <span>DB Record</span>
      </button>

      {open ? (
        <RecordInspector
          endpoint={`/api/artist/${encodeURIComponent(String(artist.id))}/record`}
          name={artist.name || 'Artist'}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}
