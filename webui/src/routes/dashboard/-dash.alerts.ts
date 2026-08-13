/**
 * Pure core for the dashboard's Alerts band — the exception surface that
 * earns the page its calm: invisible while everything is healthy, a slim
 * band of amber rows when a core connection is down.
 *
 * Health is read through the SAME presentation logic the retired Services
 * card used (-dash.services metadataSourceCard / genericServiceCard /
 * soulseekCard, each differentially tested against the vanilla), so an
 * alert fires exactly when the old card's indicator left its 'connected'
 * state — no second opinion about what "down" means.
 */

import type { ServiceStatusPayload } from './-dash.api';

import { genericServiceCard, metadataSourceCard, soulseekCard } from './-dash.services';

export interface DashAlert {
  key: 'metadata' | 'server' | 'download';
  label: string;
  /** The service view's own status text ("Disconnected", "Rate limited"...). */
  detail: string;
  /** 'error' renders red, everything else amber. */
  severity: 'warn' | 'error';
}

function severityOf(indicatorClass: string): 'warn' | 'error' {
  return indicatorClass.includes('error') ? 'error' : 'warn';
}

/** Alerts for the current /status payload; [] while healthy or before the
 *  first payload (null) — a booting page shows no false alarms. */
export function serviceAlerts(payload: ServiceStatusPayload | null): DashAlert[] {
  if (!payload) return [];
  const alerts: DashAlert[] = [];

  const meta = metadataSourceCard(payload.metadata_source || {}, payload.spotify || {});
  if (!meta.indicatorClass.includes(' connected')) {
    alerts.push({
      key: 'metadata',
      label: meta.title || 'Metadata Source',
      detail: meta.statusText,
      severity: severityOf(meta.indicatorClass),
    });
  }

  const server = genericServiceCard(payload.media_server || {});
  if (!server.indicatorClass.includes(' connected')) {
    alerts.push({
      key: 'server',
      label: 'Media Server',
      detail: server.statusText,
      severity: severityOf(server.indicatorClass),
    });
  }

  const dl = soulseekCard(payload.soulseek || {});
  if (!dl.indicatorClass.includes(' connected')) {
    alerts.push({
      key: 'download',
      label: dl.title || 'Download Source',
      detail: dl.statusText,
      severity: severityOf(dl.indicatorClass),
    });
  }

  return alerts;
}
