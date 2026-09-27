import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import { fetchDiscoverLayout, saveDiscoverLayout } from './-discover.api';
import type { DiscoverLayoutSection } from './-discover.layout';

/**
 * The layout API contract: GET reads the profile's layout, PUT replaces it
 * with exactly the section list it was given, and transport failures
 * surface as thrown errors.
 */
describe('discover layout api', () => {
  it('fetches the profile layout', async () => {
    const sections: DiscoverLayoutSection[] = [{ id: 'adv-wave', zone: 'for-you', enabled: true, position: 0 }];
    server.use(
      http.get('/api/discover/layout', () => HttpResponse.json({ success: true, sections })),
    );
    const data = await fetchDiscoverLayout();
    expect(data.success).toBe(true);
    expect(data.sections).toEqual(sections);
  });

  it('saves the section list it was given', async () => {
    let body: unknown;
    server.use(
      http.put('/api/discover/layout', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ success: true, sections: [] });
      }),
    );
    const sections: DiscoverLayoutSection[] = [{ id: 'adv-wave', zone: 'for-you', enabled: false, position: 0 }];
    const data = await saveDiscoverLayout(sections);
    expect(body).toEqual({ sections });
    expect(data.success).toBe(true);
  });

  it('surfaces server errors as thrown errors', async () => {
    server.use(
      http.get('/api/discover/layout', () =>
        HttpResponse.json({ success: false, error: 'nope' }, { status: 500 }),
      ),
    );
    await expect(fetchDiscoverLayout()).rejects.toThrow();
  });
});
