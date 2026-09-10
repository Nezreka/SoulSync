import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, expect, it, vi } from 'vitest';

const source = readFileSync(resolve(process.cwd(), 'static/setup-wizard.js'), 'utf8');
let fetcher: ReturnType<typeof vi.fn>;
let wizard: any;
beforeEach(() => {
  document.body.innerHTML =
    '<div id="setup-wizard-overlay"><div id="setup-wizard-content"></div></div>';
  localStorage.clear();
  fetcher = vi.fn(async () => new Response(JSON.stringify({ success: true })));
  wizard = new Function(
    'document',
    'window',
    'fetch',
    'localStorage',
    `${source};
    return { next: wizardNext, close: closeSetupWizard, finish: _wizardFinish, open: openSetupWizard,
      step: () => _wizardStep, setStep: value => { _wizardStep = value; },
      settings: () => _wizardSettings };
  `,
  )(document, window, fetcher, localStorage);
  wizard.open();
  wizard.setStep(1);
});
it('waits for a saved step and ignores duplicate Next', async () => {
  let done!: (value: Response) => void;
  fetcher.mockImplementation(
    () =>
      new Promise((resolve) => {
        done = resolve;
      }),
  );
  const pending = wizard.next();
  await wizard.next();
  expect(wizard.step()).toBe(1);
  expect(fetcher).toHaveBeenCalledTimes(1);
  done(new Response(JSON.stringify({ success: true })));
  await pending;
  expect(wizard.step()).toBe(2);
});
it.each([400, 200])('keeps the step and reports a rejected save (HTTP %s)', async (status) => {
  fetcher.mockResolvedValue(
    new Response(JSON.stringify({ success: false, error: 'Settings rejected' }), { status }),
  );
  await wizard.next();
  expect(wizard.step()).toBe(1);
  expect(document.querySelector('[role="alert"]')?.textContent).toBe('Settings rejected');
});
it('does not mark dismissal complete and preserves in-tab progress', () => {
  wizard.close();
  expect(fetcher).not.toHaveBeenCalled();
  expect(localStorage.getItem('soulsync_setup_complete')).toBeNull();
  wizard.open();
  expect(wizard.step()).toBe(1);
});
it('does not complete setup after a failed final settings save', async () => {
  fetcher.mockRejectedValue(new Error('Offline'));
  await wizard.finish();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(localStorage.getItem('soulsync_setup_complete')).toBeNull();
  expect(document.getElementById('setup-wizard-overlay')?.style.display).toBe('flex');
});
it('requires server confirmation before marking Finish complete', async () => {
  fetcher
    .mockResolvedValueOnce(new Response(JSON.stringify({ success: true })))
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ success: false, error: 'Try again' }), { status: 503 }),
    );
  await wizard.finish();
  expect(localStorage.getItem('soulsync_setup_complete')).toBeNull();
  fetcher.mockImplementation(async () => new Response(JSON.stringify({ success: true })));
  await wizard.finish();
  expect(localStorage.getItem('soulsync_setup_complete')).toBe('true');
});
