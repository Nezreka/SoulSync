/**
 * The organize-by-playlist toggle for the discovery modal footer — the
 * spotify_public-only control the vanilla renders via
 * discoveryModalOrganizeFooterHtml + playlistOrganizeToggleHtml
 * (sync-services.js 9552-9585, shared-helpers.js 1312-1330): reads the
 * mirrored playlist's preference on mount, PATCHes on change.
 *
 * Declared divergence: the vanilla leaves the checkbox at the NEW value when
 * the write fails (1514-1520) — this reverts it, which reflects reality; the
 * failure toast keeps the vanilla's mirror-first hint. The vanilla's
 * quality-profile select in this footer is a P5 item (it needs the profiles
 * api + hydration protocol).
 */

import { useEffect, useState } from 'react';

import { fetchOrganizePreference, setOrganizePreference } from '../-sync.fix';

export interface OrganizeToggleProps {
  /** The mirror-resolution ref — the download vpid (spotify_public_<hash>). */
  playlistRef: string;
  /** The mirror source hint ('spotify_public'). */
  source: string;
}

export function OrganizeToggle({ playlistRef, source }: OrganizeToggleProps) {
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void fetchOrganizePreference(playlistRef, source).then((value) => {
      if (!cancelled) setEnabled(value);
    });
    return () => {
      cancelled = true;
    };
  }, [playlistRef, source]);

  const onChange = async (next: boolean) => {
    setEnabled(next);
    setBusy(true);
    const ok = await setOrganizePreference(playlistRef, source, next);
    setBusy(false);
    if (!ok) {
      setEnabled(!next);
      window.showToast?.(
        'Could not save playlist folder preference (mirror this playlist first)',
        'warning',
      );
    }
  };

  return (
    <div className="modal-footer-organize">
      <label
        className="playlist-modal-organize-toggle"
        title="Download into a playlist-named folder instead of Artist/Album"
      >
        <input
          type="checkbox"
          checked={enabled}
          disabled={busy}
          onChange={(e) => void onChange(e.target.checked)}
        />
        <span>Organize by playlist</span>
      </label>
    </div>
  );
}
