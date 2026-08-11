/** The Alerts band's pure core — fires exactly when the retired Services
 *  card's indicators left 'connected', via the same -dash.services views. */

import { describe, expect, it } from 'vitest';

import type { ServiceStatusPayload } from './-dash.api';

import { serviceAlerts } from './-dash.alerts';

const HEALTHY = {
  metadata_source: { source: 'spotify', connected: true },
  spotify: { authenticated: true },
  media_server: { connected: true },
  soulseek: { connected: true },
} as unknown as ServiceStatusPayload;

describe('serviceAlerts', () => {
  it('is silent before the first payload and while healthy', () => {
    expect(serviceAlerts(null)).toEqual([]);
    expect(serviceAlerts(HEALTHY)).toEqual([]);
  });

  it('fires one row per disconnected core service', () => {
    const alerts = serviceAlerts({
      ...HEALTHY,
      media_server: { connected: false },
      soulseek: { connected: false },
    } as unknown as ServiceStatusPayload);
    expect(alerts.map((a) => a.key)).toEqual(['server', 'download']);
    expect(alerts[0].detail).toBe('Disconnected');
    expect(alerts.every((a) => a.severity === 'warn')).toBe(true);
  });

  it('a rate-limited Spotify is attention-worthy too, with the countdown', () => {
    const alerts = serviceAlerts({
      ...HEALTHY,
      spotify: {
        authenticated: true,
        rate_limited: true,
        rate_limit: { remaining_seconds: 120 },
      },
    } as unknown as ServiceStatusPayload);
    expect(alerts.map((a) => a.key)).toEqual(['metadata']);
    expect(alerts[0].detail).toBe('Spotify paused — 2m 0s');
  });

  it('a payload with no metadata source at all alerts as disconnected', () => {
    const alerts = serviceAlerts({
      ...HEALTHY,
      metadata_source: {},
      spotify: {},
    } as unknown as ServiceStatusPayload);
    expect(alerts.map((a) => a.key)).toEqual(['metadata']);
    expect(alerts[0].detail).toBe('Disconnected');
  });
});
