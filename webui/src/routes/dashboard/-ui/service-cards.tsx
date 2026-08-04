/**
 * The Service Status card (dash-card data-card="services") — the three service
 * cards + the legacy enrichment-pills section. Markup transcribed 1:1 from
 * index.html (artefact differential pins it).
 *
 * Data: `ss:service-status`, dispatched by BOTH halves of the vanilla twin
 * pair — handleServiceStatusUpdate (socket) and fetchAndUpdateServiceStatus
 * (the app-wide 5s HTTP poller, which early-returns while the socket is
 * connected, so exactly one source dispatches at a time). Plus one mount
 * hydrate so the card doesn't wait for the next push.
 *
 * The pills section stays display:none exactly like the vanilla ("Legacy
 * pills — hidden when rate monitor active") — but it still RENDERS the chips,
 * because the vanilla grid is populated on every status update and the tour
 * highlights #enrichment-pills-section.
 */

import { useCallback, useEffect, useState } from 'react';

import type { ServiceStatusPayload } from '../-dash.api';
import type {
  EnrichmentChipView,
  EnrichmentServicePayload,
  ServiceCardView,
} from '../-dash.services';

import { fetchServiceStatus } from '../-dash.api';
import { useJiosaavnExperimentalEvent, useServiceStatusEvent } from '../-dash.events';
import {
  enrichmentChips,
  genericServiceCard,
  metadataSourceCard,
  soulseekCard,
} from '../-dash.services';

interface ServiceCardsState {
  ready: boolean;
  meta: ServiceCardView | null;
  server: ServiceCardView | null;
  soulseek: ServiceCardView | null;
  /** Materialized titles — a payload without a source KEEPS the previous
   *  title, like the vanilla's conditional writes. */
  metaTitle: string;
  dlTitle: string;
  /** The metadata Test button's target (the vanilla rewrites its onclick). */
  testService: string | null;
  chips: EnrichmentChipView[];
  /** The last raw enrichment payload — syncJiosaavnEnrichmentBubble replays
   *  the cached payload through renderEnrichmentCards on a live experimental
   *  toggle, so the chips must be recomputable without a fresh push. */
  lastEnrichment: Record<string, EnrichmentServicePayload> | null;
}

const INITIAL: ServiceCardsState = {
  ready: false,
  meta: null,
  server: null,
  soulseek: null,
  metaTitle: 'Metadata Source',
  dlTitle: 'Download Source',
  testService: null,
  chips: [],
  lastEnrichment: null,
};

export function useServiceCards(): ServiceCardsState {
  const [state, setState] = useState(INITIAL);

  const apply = useCallback((payload: ServiceStatusPayload) => {
    // The experimental flags are read LIVE on each render, exactly as
    // renderEnrichmentCards does.
    const jiosaavnEnabled = window.isJiosaavnExperimentalEnabled?.() ?? false;
    const bandcampEnabled = window.isBandcampExperimentalEnabled?.() ?? false;
    const meta = metadataSourceCard(payload.metadata_source || {}, payload.spotify || {});
    const server = genericServiceCard(payload.media_server || {});
    const soulseek = soulseekCard(payload.soulseek || {});
    const enrichment = payload.enrichment as Record<string, EnrichmentServicePayload> | undefined;
    setState((prev) => ({
      ready: true,
      meta,
      server,
      soulseek,
      metaTitle: meta.title ?? prev.metaTitle,
      dlTitle: soulseek.title ?? prev.dlTitle,
      testService: meta.testService ?? prev.testService,
      // The vanilla only re-renders the chips when the payload carries
      // `enrichment` — otherwise the grid keeps its previous content.
      chips: enrichment
        ? enrichmentChips(enrichment, jiosaavnEnabled, bandcampEnabled)
        : prev.chips,
      lastEnrichment: enrichment ?? prev.lastEnrichment,
    }));
  }, []);

  useServiceStatusEvent(apply);
  // A live experimental toggle replays the cached payload through the chip
  // renderer — exactly what syncJiosaavnEnrichmentBubble does with
  // _lastStatusPayload.enrichment.
  useJiosaavnExperimentalEvent(
    useCallback((frame) => {
      setState((prev) => {
        if (!prev.lastEnrichment) return prev;
        const bandcampEnabled = window.isBandcampExperimentalEnabled?.() ?? false;
        return {
          ...prev,
          chips: enrichmentChips(prev.lastEnrichment, frame.enabled === true, bandcampEnabled),
        };
      });
    }, []),
  );

  useEffect(() => {
    let cancelled = false;
    void fetchServiceStatus().then((payload) => {
      if (!cancelled && payload) apply(payload);
    });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  return state;
}

function openChipSettings(selector: string) {
  // The vanilla chip onclick, verbatim timings: navigate → switch tab → scroll.
  void window.SoulSyncWebRouter?.navigateToPage('settings');
  setTimeout(() => {
    window.switchSettingsTab?.('connections');
    setTimeout(() => {
      const el = document.querySelector(selector);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 100);
  }, 50);
}

function ServiceCard({
  cardId,
  titleId,
  title,
  indicatorId,
  textId,
  responseId,
  view,
  ready,
  onTest,
}: {
  cardId: string;
  titleId: string;
  title: string;
  indicatorId: string;
  textId: string;
  responseId: string;
  view: ServiceCardView | null;
  ready: boolean;
  onTest: () => void;
}) {
  return (
    <div className="service-card" id={cardId} data-status-ready={ready ? 'true' : 'false'}>
      <div className="service-card-header">
        <span className="service-card-title" id={titleId}>
          {title}
        </span>
        <span
          className={view ? view.indicatorClass : 'service-card-indicator disconnected'}
          id={indicatorId}
        >
          ●
        </span>
      </div>
      <p className={view ? view.statusTextClass : 'service-card-status-text'} id={textId}>
        {view ? view.statusText : 'Disconnected'}
      </p>
      <p className="service-card-response-time" id={responseId}>
        {view ? view.responseText : 'Response: --'}
      </p>
      <div className="service-card-footer">
        <button className="service-card-button" onClick={onTest}>
          Test
        </button>
      </div>
    </div>
  );
}

export function ServiceStatusCard() {
  const { ready, meta, server, soulseek, metaTitle, dlTitle, testService, chips } =
    useServiceCards();

  return (
    <article className="dash-card" data-card="services">
      <header className="dash-card__head">
        <h3 className="dash-card__title">Service Status</h3>
        <p className="dash-card__sub">Connection health for every service SoulSync uses.</p>
      </header>
      <div className="dash-card__body">
        <div className="service-status-grid">
          <ServiceCard
            cardId="metadata-source-service-card"
            titleId="metadata-source-title"
            title={metaTitle}
            indicatorId="metadata-source-status-indicator"
            textId="metadata-source-status-text"
            responseId="metadata-source-response-time"
            view={meta}
            ready={ready}
            onTest={() =>
              // The vanilla button starts as testDashboardConnection(
              // getActiveMetadataSource()) and is rewritten to the payload's
              // literal source once one arrives.
              window.testDashboardConnection?.(
                testService ?? window.getActiveMetadataSource?.() ?? 'spotify',
              )
            }
          />
          <ServiceCard
            cardId="media-server-service-card"
            titleId="media-server-service-name"
            title="Media Server"
            indicatorId="media-server-status-indicator"
            textId="media-server-status-text"
            responseId="media-server-response-time"
            view={server}
            ready={ready}
            onTest={() => window.testDashboardConnection?.('server')}
          />
          <ServiceCard
            cardId="soulseek-service-card"
            titleId="download-source-title"
            title={dlTitle}
            indicatorId="soulseek-status-indicator"
            textId="soulseek-status-text"
            responseId="soulseek-response-time"
            view={soulseek}
            ready={ready}
            onTest={() => window.testDashboardConnection?.('soulseek')}
          />
        </div>
        <div
          className="enrichment-section"
          id="enrichment-pills-section"
          style={{ display: 'none' }}
        >
          <div className="enrichment-section-header">
            <span className="enrichment-section-label">Enrichment Services</span>
          </div>
          <div className="enrichment-status-grid" id="enrichment-status-grid">
            {chips.map((chip) => (
              <div
                key={chip.key}
                className={`enrichment-chip status-${chip.statusClass}`}
                title={chip.tooltip}
                onClick={
                  chip.settingsSelector ? () => openChipSettings(chip.settingsSelector!) : undefined
                }
              >
                <span className="enrichment-chip-dot"></span>
                <span className="enrichment-chip-name">{chip.name}</span>
                {chip.activity ? (
                  <span className="enrichment-chip-activity">{chip.activity}</span>
                ) : null}
                <span className="enrichment-chip-status">{chip.statusDisplay}</span>
                {chip.budget ? (
                  <div className="enrichment-chip-budget">
                    <div
                      className={
                        chip.budget.barClass
                          ? `enrichment-chip-budget-bar ${chip.budget.barClass}`
                          : 'enrichment-chip-budget-bar'
                      }
                      style={{ width: `${chip.budget.pct}%` }}
                    ></div>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      </div>
    </article>
  );
}
