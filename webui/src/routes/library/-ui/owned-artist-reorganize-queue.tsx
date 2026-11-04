import { useQueryClient } from '@tanstack/react-query';

import { LIBRARY_V2_QUERY_KEY } from '../-library-v2.api';
import { ReorganizeStatusPanel } from '../../artist-detail/-ui/reorganize-status-panel';

// dev's live reorganize queue panel, with its cancel and clear, on the owned
// view. it shows only while something is queued, running or just finished.
export function OwnedArtistReorganizeQueue({ artistId }: { artistId: number }) {
  const queryClient = useQueryClient();
  return (
    <ReorganizeStatusPanel
      artistId={artistId}
      onReload={() => void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY })}
    />
  );
}
