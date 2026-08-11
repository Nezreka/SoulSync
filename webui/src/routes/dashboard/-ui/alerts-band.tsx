/**
 * The Alerts band (dash-card data-card="alerts") — the dashboard's exception
 * surface. Renders NOTHING while every core connection is healthy; when one
 * isn't, a slim amber/red band appears at the very top of the grid with one
 * row per problem and a jump to Settings. This is what lets the rest of the
 * page stay calm: ops moved to the tray/sidebar/modals, and the band is the
 * one place that shouts when something actually needs a human.
 *
 * Data: the same /status payload + ss:service-status push the sidebar dots
 * use; health semantics come from -dash.services' differentially-tested card
 * views via -dash.alerts.
 */

import { useCallback, useEffect, useState } from 'react';

import type { ServiceStatusPayload } from '../-dash.api';

import { serviceAlerts } from '../-dash.alerts';
import { fetchServiceStatus } from '../-dash.api';
import { useServiceStatusEvent } from '../-dash.events';

export function AlertsBand() {
  const [payload, setPayload] = useState<ServiceStatusPayload | null>(null);

  useServiceStatusEvent(useCallback((p) => setPayload(p), []));
  useEffect(() => {
    let cancelled = false;
    void fetchServiceStatus().then((p) => {
      if (!cancelled && p) setPayload(p);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const alerts = serviceAlerts(payload);
  if (alerts.length === 0) return null;

  return (
    <article className="dash-card dash-alerts" data-card="alerts" role="alert">
      {alerts.map((a) => (
        <div key={a.key} className={`dash-alert dash-alert--${a.severity}`}>
          <span className="dash-alert-dot"></span>
          <span className="dash-alert-label">{a.label}</span>
          <span className="dash-alert-detail">{a.detail}</span>
          <button
            type="button"
            className="dash-alert-action"
            onClick={() => void window.SoulSyncWebRouter?.navigateToPage('settings')}
          >
            Open Settings
          </button>
        </div>
      ))}
    </article>
  );
}
