/**
 * Mounts the feature picker in its own root, so the sidebar's Customize
 * button, settings and the setup flow can all open it with
 * window.openFeaturePicker(), whatever page is showing.
 */

import { createRoot } from 'react-dom/client';

import { FeaturePicker } from './feature-picker';

const HOST_ID = 'soulsync-feature-picker-root';

export function mountFeaturePickerHost(): void {
  if (typeof document === 'undefined' || document.getElementById(HOST_ID)) return;
  const el = document.createElement('div');
  el.id = HOST_ID;
  document.body.appendChild(el);
  createRoot(el).render(<FeaturePicker />);
}
