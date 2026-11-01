import { createFileRoute } from '@tanstack/react-router';
import { z } from 'zod';

import { LabelDetailPage } from './-ui/label-detail-page';

// The label's display name travels as a search param so a refresh / direct
// load has something to show before the catalog fetch resolves the canonical
// name. TanStack JSON-parses param values, so an all-digits name would arrive
// as a NUMBER and a bare z.string() would throw — coerce it back to a string
// (same guard as the artist-detail route).
const labelDetailSearchSchema = z.object({
  name: z
    .preprocess(
      (v) =>
        typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean' ? String(v) : '',
      z.string(),
    )
    .optional()
    .default(''),
});

export const Route = createFileRoute('/label-detail/$id')({
  validateSearch: labelDetailSearchSchema,
  component: LabelDetailRoute,
});

function LabelDetailRoute() {
  const { id } = Route.useParams();
  const { name } = Route.useSearch();
  // Keyed by id so a label → label hop remounts rather than threading a reset
  // through every piece of page state.
  return <LabelDetailPage key={id} labelId={id} labelName={name} />;
}
