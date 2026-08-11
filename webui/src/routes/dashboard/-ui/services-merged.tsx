/**
 * The Services card — connection health (the three service tiles + enrichment
 * chips). The API-rate equalizer used to be merged in here; it now renders as
 * the page's ambient orb field (see dashboard-page.tsx), so this card is back
 * to one subject. ServiceStatusCard keeps its own component, data flow and
 * ids — this card only supplies the shell, so the artefact tests that pin the
 * half's standalone shape against the vanilla fixture keep passing untouched.
 */

import { ServiceStatusCard } from './service-cards';

export function ServicesCard() {
  return (
    <article className="dash-card" data-card="services">
      <header className="dash-card__head">
        <h3 className="dash-card__title">Services</h3>
        <p className="dash-card__sub">Connection health.</p>
      </header>
      <div className="dash-card__body">
        <ServiceStatusCard embedded />
      </div>
    </article>
  );
}
