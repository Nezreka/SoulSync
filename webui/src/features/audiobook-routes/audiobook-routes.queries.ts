import { queryOptions, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  AUDIOBOOK_ROUTES_QUERY_KEY,
  fetchFolderConfigured,
  fetchRoutedNames,
  moveExistingAlbums,
  refreshAudiobookMetadata,
  setAudiobookRoute,
  summariseMove,
} from './audiobook-routes.api';

export function routedNamesQueryOptions(names: string[]) {
  // Sorted and de-duplicated so the same set of cards shares one cache entry
  // however the page happens to order them.
  const unique = [...new Set(names.filter((name) => name.trim()))].sort();
  return queryOptions({
    queryKey: [...AUDIOBOOK_ROUTES_QUERY_KEY, 'check', unique] as const,
    queryFn: () => fetchRoutedNames(unique),
    enabled: unique.length > 0,
    staleTime: 30_000,
  });
}

/** Which of these artist names are routed to audiobooks (empty set while loading). */
export function useRoutedArtists(names: string[]): Set<string> {
  const query = useQuery(routedNamesQueryOptions(names));
  return new Set(query.data ?? []);
}

/** Whether an audiobook folder is set. Unknown counts as set: the server has the last word. */
export function useAudiobookFolderConfigured(): boolean {
  const query = useQuery({
    queryKey: [...AUDIOBOOK_ROUTES_QUERY_KEY, 'folder'] as const,
    queryFn: fetchFolderConfigured,
    staleTime: 30_000,
  });
  return query.data !== false;
}

/**
 * Turn routing on or off for one artist. Turning it ON offers to move what is
 * already in the music library, because routing only affects new downloads and
 * the old albums would otherwise stay behind in the wrong server.
 */
export function useSetAudiobookRoute(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (routed: boolean) => {
      await setAudiobookRoute(name, routed);
      window.showToast?.(
        routed
          ? `${name}: new downloads are filed as audiobooks`
          : `${name}: downloads are filed as music again`,
        'success',
      );
      if (!routed) return;
      try {
        await offerToMoveExisting(name);
      } finally {
        // Albums already in the audiobook folder (moved by hand, or just now)
        // get their metadata.opf so the media server sees series and episode.
        await refreshAudiobookMetadata(name).catch(() => undefined);
      }
    },
    onError: (error: Error) => window.showToast?.(error.message, 'error'),
    onSettled: () => queryClient.invalidateQueries({ queryKey: AUDIOBOOK_ROUTES_QUERY_KEY }),
  });
}

/** Preview what is still in the music library; move it only if the user agrees. */
async function offerToMoveExisting(name: string): Promise<void> {
  const preview = await moveExistingAlbums(name, false);
  const { toMove, clashes } = summariseMove(preview);
  if (toMove === 0) return;
  const note = clashes > 0 ? ` ${clashes} album(s) already exist there and stay as they are.` : '';
  const confirmed = await window.showConfirmDialog?.({
    title: 'Move albums',
    message: `Move ${toMove} album folder(s) of ${name} from the music library to the audiobook folder?${note} Your media server needs a library scan afterwards.`,
    confirmText: 'Move',
  });
  if (!confirmed) return;
  const done = await moveExistingAlbums(name, true);
  window.showToast?.(
    `Moved ${done.moved} album(s)${done.failed ? `, ${done.failed} failed` : ''}`,
    done.failed ? 'error' : 'success',
  );
}
