/**
 * Mounts the setup flow in its own root and gives the vanilla shell the
 * same two globals the old wizard did: openSetupWizard and closeSetupWizard.
 * init.js opens it on first run and hands over _onSetupWizardComplete, which
 * runs once the flow closes (finished or "Finish later") so the app carries
 * on booting. The developer preview opens it with { preview: true }.
 */

import { createRoot } from 'react-dom/client';

import type { OpenOptions } from './types';

import { Onboarding } from './onboarding';
import { useOnboarding } from './store';

const HOST_ID = 'soulsync-onboarding-root';
export const READY_EVENT = 'soulsync:onboarding-ready';

declare global {
  interface Window {
    openSetupWizard?: (options?: OpenOptions) => void;
    closeSetupWizard?: () => void;
    _onSetupWizardComplete?: (() => void) | null;
  }
}

export function openOnboarding(options: OpenOptions = {}) {
  useOnboarding.getState().begin(options);
}

/**
 * Settings > Advanced > Developer > Setup preview. The markup is static in
 * index.html, next to the weather preview; this only wires the button.
 */
export function wireSetupPreviewControls(root: ParentNode = document): void {
  const button = root.querySelector<HTMLButtonElement>('#ob-preview-open');
  if (!button || button.dataset.wired) return;
  button.dataset.wired = '1';
  button.addEventListener('click', () => {
    const step = root.querySelector<HTMLSelectElement>('#ob-preview-step')
      ?.value as OpenOptions['startAt'];
    const pretend = root.querySelector<HTMLSelectElement>('#ob-preview-env')
      ?.value as OpenOptions['pretend'];
    openOnboarding({ preview: true, startAt: step || 'welcome', pretend: pretend || 'real' });
  });
}

export function mountOnboardingHost(): void {
  if (typeof document === 'undefined' || document.getElementById(HOST_ID)) return;
  const el = document.createElement('div');
  el.id = HOST_ID;
  document.body.appendChild(el);
  createRoot(el).render(<Onboarding />);

  useOnboarding.subscribe((state, prev) => {
    if (!prev.open || state.open || prev.preview) return;
    const done = window._onSetupWizardComplete;
    window._onSetupWizardComplete = null;
    if (typeof done === 'function') done();
  });

  wireSetupPreviewControls();
  window.openSetupWizard = openOnboarding;
  window.closeSetupWizard = () => useOnboarding.getState().close();
  window.dispatchEvent(new Event(READY_EVENT));
}
