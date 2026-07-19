import { createFileRoute } from '@tanstack/react-router';
import { useLayoutEffect } from 'react';
import { z } from 'zod';

import { useShellBridge } from '@/platform/shell/route-controllers';

// The label's display name travels as a search param so a refresh / direct
// load has something to show before the catalog fetch resolves the canonical
// name. TanStack JSON-parses param values, so an all-digits name would arrive
// as a NUMBER and a bare z.string() would throw — coerce it back to a string
// (same guard as the artist-detail route).
const labelDetailSearchSchema = z.object({
  name: z
    .preprocess((v) => (v == null ? '' : String(v)), z.string())
    .optional()
    .default(''),
});

export const Route = createFileRoute('/label-detail/$id')({
  validateSearch: labelDetailSearchSchema,
  component: LabelDetailPage,
});

// Thin legacy handoff: TanStack owns the URL shape (/label-detail/:id), but the
// vanilla JS label-detail page still renders the actual experience. Mounting
// this route (click OR refresh) hands the id back to the vanilla renderer.
function LabelDetailPage() {
  const bridge = useShellBridge();
  const { id } = Route.useParams();
  const { name } = Route.useSearch();

  useLayoutEffect(() => {
    if (!bridge) return;
    bridge.navigateToLabelDetail(id, name, { skipRouteChange: true });
  }, [bridge, id, name]);

  return null;
}
