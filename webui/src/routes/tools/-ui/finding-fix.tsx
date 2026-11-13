/**
 * Fixing one finding: the per-type question, the confirm for a delete, and
 * the toast with the result.
 *
 * Advanced mode's list and album tray and Simple mode's album tray all fix
 * through this, so a row asks the same thing wherever it's clicked. Simple
 * mode used to post a bare fix: no orphan / dead file / acoustid / quality
 * question, no "Move ... to the deleted-files folder?" before a subjectless
 * Delete File, and no toast either way (review 5 W3-TT-3).
 */

import { useCallback } from 'react';

import type { FindingTypeInfo } from '../-tools.groups';
import type { RepairFinding } from '../-tools.types';
import type { FindingPrompts } from './finding-prompts';

import { findExactArtist, fixFinding, searchLibraryArtists } from '../-tools.api';
import { findingFilePath, findingRowFixLabel } from '../-tools.core';
import { ReidentifyModal } from '../../artist-detail/-ui/reidentify-modal';

function toast(message: string, type = 'info') {
  window.showToast?.(message, type);
}

/** Finding types whose fix needs a question answered before it can run. Keyed
 *  by TYPE now, not by job — a job can emit more than one type. */
export const TYPE_ORPHAN = 'orphan_file';
export const TYPE_DEAD = 'dead_file';
export const TYPE_UNREACHABLE = 'library_unreachable';
export const TYPE_ACOUSTID = 'acoustid_mismatch';
export const TYPE_RETAG = 'library_retag';
export const TYPE_BACKFILL = 'missing_discography_track';
export const TYPE_QUALITY = 'quality_upgrade';
export const TYPE_SUSPECT_ALBUM = 'suspect_album_tag';

/**
 * A poisoned album holds tracks by many artists, so it can't be re-identified
 * as one release. Open it in the library instead, where each track has its own
 * Re-identify (#1567). Findings from before the artist id was stored find the
 * artist by the album artist's name.
 */
export async function openSuspectAlbum(finding: RepairFinding): Promise<void> {
  const d = (finding.details as Record<string, any>) || {};
  const router = window.SoulSyncWebRouter;
  // native findings name the album as `lib2:<id>`; the library route wants the id
  const albumId =
    finding.entity_id != null ? String(finding.entity_id).replace(/^lib2:/, '') : null;
  let artistId = d.artist_id != null && d.artist_id !== '' ? String(d.artist_id) : '';
  let artistName = String(d.album_artist_name || '');
  if (!artistId && artistName) {
    try {
      const exact = findExactArtist(await searchLibraryArtists(artistName), artistName);
      if (exact) {
        artistId = String(exact.id);
        artistName = exact.name || artistName;
      }
    } catch {
      // falls through to the toast below
    }
  }
  if (!artistId || !router) {
    toast("Couldn't find this album in your library", 'error');
    return;
  }
  toast('Open each track there and pick Re-identify from its menu', 'info');
  await router.navigateToPage('artist-detail', {
    artistId,
    artistName,
    focusAlbumId: albumId ?? undefined,
  });
}

export interface FindingFixOptions {
  video: boolean;
  typeInfo: (findingType: string) => FindingTypeInfo | undefined;
  prompts: FindingPrompts;
  /** dismiss one finding (quality "Ignore", backfill "Just Clear") */
  dismiss: (id: number) => Promise<void>;
  /** reload whatever shows the finding */
  refresh: () => void;
  /** a duplicate release is settled in its detail panel; open it */
  onExpand?: (finding: RepairFinding) => void;
  /** a suspect track opens the re-identify modal */
  onReidentify: (finding: RepairFinding) => void;
  /** the row's button is busy while its fix is out */
  setBusy?: (id: number, busy: boolean) => void;
}

export function useFindingFix({
  video,
  typeInfo,
  prompts,
  dismiss,
  refresh,
  onExpand,
  onReidentify,
  setBusy,
}: FindingFixOptions) {
  return useCallback(
    async (finding: RepairFinding, override?: string) => {
      const type = finding.finding_type;
      let fixAction: string | null = override ?? null;
      if (video) {
        // video types share some slugs with music (quality_upgrade) but none
        // of music's questions. a fix that changes a file on disk asks once,
        // in the words the catalog has for it.
        const info = typeInfo(type);
        if (!override && info?.destructive) {
          const confirmed = await window.showConfirmDialog?.({
            title: `${info.verb || 'Fix'}: ${finding.title}`,
            message: info.confirm || 'This changes a file on disk.',
            confirmText: info.verb || 'Fix',
            destructive: true,
          });
          if (!confirmed) return;
        }
      } else if (!override) {
        if (type === 'native_duplicate_releases') {
          if (onExpand) onExpand(finding);
          else toast('Pick the release to keep from this finding in Advanced.', 'info');
          return;
        }
        if (type === TYPE_ORPHAN) {
          fixAction = await prompts.promptOrphan();
          if (!fixAction) return;
        }
        if (type === TYPE_DEAD) {
          fixAction = await prompts.promptDeadFile();
          if (!fixAction) return;
        }
        if (type === TYPE_UNREACHABLE) {
          // the one answer the outage guards can't work out for themselves
          const outage = (finding.details?.outage || {}) as { unreachable?: number };
          const count = Number(outage.unreachable) || 0;
          const confirmed = await window.showConfirmDialog?.({
            title: 'Are these files really gone?',
            message:
              `${count ? `${count.toLocaleString()} files` : 'The files'} that can't be reached are marked missing and listed under Dead Files, where you download them again or remove them.\n\n` +
              'With "Re-download files deleted outside SoulSync" on, monitored tracks go back to the Wishlist.\n\n' +
              'If a drive is disconnected or a path mapping changed, cancel and fix that. Nothing is lost by waiting.',
            confirmText: 'They are gone',
            cancelText: 'Cancel',
            destructive: true,
          });
          if (!confirmed) return;
        }
        if (type === TYPE_ACOUSTID) {
          // an ambiguous finding carries its candidate recordings - hand them to
          // the dialog so the user can pick which one to retag/relocate as
          const d = (finding.details || {}) as {
            ambiguous?: boolean;
            candidates?: string[];
          };
          fixAction = await prompts.promptAcoustid(d.ambiguous ? d.candidates : undefined);
          if (!fixAction) return;
        }
        if (type === TYPE_QUALITY) {
          fixAction = await prompts.promptQuality();
          if (!fixAction) return;
          if (fixAction === 'ignore') {
            await dismiss(finding.id);
            return;
          }
        }
        if (type === TYPE_RETAG && !finding.details?.has_manual_conflict) {
          // Nothing to settle on this row — the plain apply is the whole action.
        } else if (type === TYPE_RETAG) {
          fixAction = await prompts.promptRetag(1, 1);
          if (!fixAction) return;
          if (fixAction === 'safe') fixAction = null;
        }
        if (type === TYPE_BACKFILL) {
          const choice = await prompts.promptBackfill(1);
          if (!choice) return;
          if (choice === 'dismiss') {
            await dismiss(finding.id);
            return;
          }
          // 'add_to_wishlist' falls through with no fix_action — the handler
          // already adds to the wishlist by default.
        }
        if (type === TYPE_SUSPECT_ALBUM && finding.entity_type === 'album') {
          await openSuspectAlbum(finding);
          return;
        }
        if (type === TYPE_SUSPECT_ALBUM) {
          onReidentify(finding);
          return;
        }
      }

      if (!video && type === 'native_duplicate_tracks') {
        const weak = Boolean(finding.details?.requires_recording_confirmation);
        const confirmed = await window.showConfirmDialog?.({
          title: weak ? 'Confirm Recording & Keep Best' : 'Keep Best',
          message: weak
            ? 'After comparing these files, confirm they contain the same recording. Keep the recommended file and move redundant copies to recoverable quarantine? Protected copies stay.'
            : 'Keep the recommended file and move redundant copies to recoverable quarantine? Protected companions, shared files and playlist copies stay.',
          confirmText: weak ? 'Confirm Recording & Keep Best' : 'Keep Best',
          destructive: true,
        });
        if (!confirmed) return;
        fixAction = weak ? 'keep_best:confirmed' : 'keep_best';
      }

      // A finding with no catalogue subject cannot be re-downloaded — the fix
      // is a plain delete, and unlike every prompt above it has no dialog of
      // its own to stop at. Confirm it here rather than let one click remove a
      // file from disk.
      if (!video && !override && findingRowFixLabel(finding) === 'Delete File') {
        const quarantined = type === 'corrupt_audio' || type === 'fake_lossless';
        const confirmed = await window.showConfirmDialog?.({
          title: 'Delete File',
          message: quarantined
            ? `Move ${findingFilePath(finding) || 'this file'} to the deleted-files folder? It is not in your library, so nothing will be queued to replace it.`
            : `Permanently delete ${findingFilePath(finding) || 'this file'} from disk? It is not in your library, so nothing will be queued to replace it.`,
          confirmText: 'Delete',
          destructive: true,
        });
        if (!confirmed) return;
        // Fake lossless refuses to remove an uncatalogued file without being
        // told to: its default action is a re-download.
        fixAction = 'delete';
      }

      setBusy?.(finding.id, true);
      try {
        let result = await fixFinding(finding.id, fixAction);
        if (result.needs_confirm) {
          // a library track has the same filename somewhere else. show it and
          // let the user decide, instead of a dead end
          const paths = result.library_paths ?? [];
          const confirmed = await window.showConfirmDialog?.({
            title: 'Same Filename In Library',
            message: paths.length
              ? `Your library already has a file with this name:\n\n${paths.join('\n')}\n\nIf it's the same song, this orphan is a leftover copy. Delete it from disk?`
              : 'Your library already has a file with this name in another folder. If it is the same song, this orphan is a leftover copy. Delete it from disk?',
            confirmText: 'Delete anyway',
            destructive: true,
          });
          if (!confirmed) {
            setBusy?.(finding.id, false);
            refresh();
            return;
          }
          result = await fixFinding(finding.id, 'delete_confirmed');
        }
        toast(
          result.success ? result.message || 'Fixed successfully' : result.error || 'Fix failed',
          result.success ? 'success' : 'error',
        );
        refresh();
      } catch {
        toast('Error applying fix', 'error');
        // Restored ONLY on the error path. On success the button stays
        // disabled until the reload lands — which is what stops a second
        // click firing a second fix while the request is in flight.
        setBusy?.(finding.id, false);
      }
    },
    [dismiss, onExpand, onReidentify, prompts, refresh, setBusy, typeInfo, video],
  );
}

/** an error that tells you to use Re-identify, on a finding that names a
 *  track: the modal can be opened from the finding itself */
export function needsReidentify(finding: RepairFinding): boolean {
  return (
    finding.status === 'pending' &&
    finding.entity_type === 'track' &&
    finding.entity_id != null &&
    /re-identify/i.test(finding.last_error || '')
  );
}

/** The re-identify modal for a suspect_album_tag track finding. Applying it
 *  settles the finding, so it is dismissed. */
export function ReidentifyFindingModal({
  finding,
  onApplied,
  onClose,
}: {
  finding: RepairFinding;
  onApplied: () => Promise<void>;
  onClose: () => void;
}) {
  const d = (finding.details as Record<string, any>) || {};
  // a re-tag finding keeps its names on the library's side of the comparison
  const ref = (d.validation?.reference as Record<string, any>) || {};
  return (
    <ReidentifyModal
      trackId={finding.entity_id}
      trackTitle={String(d.track_title || ref.title || d.title || finding.title || '')}
      artistName={String(d.artist_name || ref.artist_name || d.artist || '')}
      albumTitle={String(d.album_title || ref.album_title || d.album || '')}
      imageUrl={String(d.album_thumb_url || finding.album_thumb_url || '')}
      initialQuery={String(d.reidentify_query || '')}
      onApplied={onApplied}
      onClose={onClose}
    />
  );
}
