/**
 * ONE Services card: connection health (the three service tiles + enrichment
 * chips) and the API-rate equalizer, which used to be two separate cards
 * saying overlapping things. Both halves keep their own components, data flow
 * and ids — this card only supplies the shared shell, so the artefact tests
 * that pin each half's standalone shape against the vanilla fixture keep
 * passing untouched.
 */

import { RateMonitorCard } from './rate-equalizer';
import { ServiceStatusCard } from './service-cards';

export function ServicesCard() {
  return (
    <article className="dash-card" data-card="services">
      <header className="dash-card__head">
        <h3 className="dash-card__title">Services</h3>
        <p className="dash-card__sub">Connection health and API rates.</p>
      </header>
      <div className="dash-card__body">
        <ServiceStatusCard embedded />
        <div className="dash-merged-divider" aria-hidden="true" />
        <RateMonitorCard embedded />
      </div>
    </article>
  );
}
