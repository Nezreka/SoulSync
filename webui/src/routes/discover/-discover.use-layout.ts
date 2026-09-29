import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';

import { fetchDiscoverLayout, saveDiscoverLayout } from './-discover.api';
import {
  defaultDiscoverLayout,
  layoutSectionsByZone,
  type DiscoverLayoutSection,
  type DiscoverSectionId,
  type DiscoverZoneId,
} from './-discover.layout';
import { profileKey } from './-discover.profile-scope';

/**
 * The discover page layout: the profile's saved sections (zone, order,
 * enabled) from GET /api/discover/layout, falling back to the defaults while
 * the fetch is pending or has failed — the page never renders without
 * sections, and no saved preferences render exactly the current order.
 */
export interface DiscoverLayoutController {
  entries: DiscoverLayoutSection[];
  sectionsByZone: Record<DiscoverZoneId, DiscoverSectionId[]>;
  isPending: boolean;
  save: (sections: DiscoverLayoutSection[]) => Promise<void>;
}

export function useDiscoverLayout(profileId: number | null): DiscoverLayoutController {
  const key = profileKey(profileId);
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['discover', 'layout', key] as const,
    queryFn: async () => {
      const data = await fetchDiscoverLayout();
      const sections = Array.isArray(data?.sections) ? data.sections : null;
      return sections?.length ? sections : defaultDiscoverLayout();
    },
    retry: false,
    staleTime: 60_000,
  });

  const save = useCallback(
    async (sections: DiscoverLayoutSection[]) => {
      await saveDiscoverLayout(sections);
      await queryClient.invalidateQueries({ queryKey: ['discover', 'layout', key] });
    },
    [queryClient, key],
  );

  const entries = query.data ?? defaultDiscoverLayout();

  return {
    entries,
    sectionsByZone: layoutSectionsByZone(entries),
    isPending: query.isPending,
    save,
  };
}
