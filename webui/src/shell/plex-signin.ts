/**
 * "sign in with plex" on the login screen. the button only shows when the
 * admin turned it on. a click opens plex's own sign-in page in a popup
 * (soulsync never sees the password), then polls until plex says yes and
 * the server has signed this browser in, and reloads into the app.
 *
 * the popup is opened straight from the click, before any request, so a
 * popup blocker doesn't eat it; its address is filled in once the pin is
 * ready.
 */

const WRAP_ID = 'login-plex';
const BUTTON_ID = 'login-plex-button';
const ERROR_ID = 'login-error';
const POLL_MS = 1500;
const GIVE_UP_MS = 4 * 60_000;

function showError(message: string): void {
  const el = document.getElementById(ERROR_ID);
  if (!el) return;
  el.textContent = message;
  el.style.display = message ? 'block' : 'none';
}

/**
 * the popup was blocked. a second window.open now would be too (it isn't
 * in the click anymore), so offer a real link: a click on it always works
 */
function showOpenLink(url: string): void {
  const el = document.getElementById(ERROR_ID);
  if (!el) return;
  el.textContent = 'Your browser blocked the Plex window. ';
  const a = document.createElement('a');
  a.href = url;
  a.target = '_blank';
  a.rel = 'noopener';
  a.textContent = 'Open Plex sign-in';
  el.appendChild(a);
  el.style.display = 'block';
}

async function post(url: string): Promise<{ ok: boolean; body: Record<string, unknown> }> {
  const resp = await fetch(url, { method: 'POST', headers: { Accept: 'application/json' } });
  let body: Record<string, unknown> = {};
  try {
    body = (await resp.json()) as Record<string, unknown>;
  } catch {
    // keep the empty body; the status says enough
  }
  return { ok: resp.ok, body };
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** the server's error message, or the fallback when it sent none */
function errorText(body: Record<string, unknown> | undefined, fallback: string): string {
  return typeof body?.error === 'string' && body.error ? body.error : fallback;
}

/** start, wait for plex, sign in. resolves true once signed in */
export async function runPlexSignIn(
  openPopup: () => Window | null = () =>
    window.open('', 'soulsync-plex-signin', 'width=520,height=720'),
  reload: () => void = () => window.location.reload(),
): Promise<boolean> {
  showError('');
  const popup = openPopup();
  const start = await post('/api/auth/plex/start').catch(() => null);
  if (!start || !start.ok || typeof start.body.url !== 'string') {
    popup?.close();
    showError(errorText(start?.body, "Couldn't reach Plex. Try again in a moment."));
    return false;
  }
  if (popup) popup.location.href = start.body.url;
  else showOpenLink(start.body.url);

  const until = Date.now() + GIVE_UP_MS;
  while (Date.now() < until) {
    await sleep(POLL_MS);
    const check = await post('/api/auth/plex/check').catch(() => null);
    if (!check) continue; // a blip; keep waiting
    if (!check.ok) {
      popup?.close();
      showError(errorText(check.body, 'Plex sign-in failed. Try again.'));
      return false;
    }
    if (check.body.pending) {
      if (popup?.closed) {
        // they closed plex's page without approving: one last look, then stop
        const last = await post('/api/auth/plex/check').catch(() => null);
        if (last?.ok && last.body.pending === false) {
          reload();
          return true;
        }
        showError('Plex sign-in was cancelled.');
        return false;
      }
      continue;
    }
    popup?.close();
    reload();
    return true;
  }
  popup?.close();
  showError('Plex sign-in timed out. Try again.');
  return false;
}

/** show the button when the admin turned plex sign-in on, and wire it once */
export async function initPlexSignIn(): Promise<void> {
  const wrap = document.getElementById(WRAP_ID);
  const button = document.getElementById(BUTTON_ID) as HTMLButtonElement | null;
  if (!wrap || !button || button.dataset.wired === '1') return;
  button.dataset.wired = '1';
  try {
    const resp = await fetch('/api/auth/plex/available', {
      headers: { Accept: 'application/json' },
    });
    const data = resp.ok ? ((await resp.json()) as { enabled?: boolean }) : {};
    wrap.hidden = data.enabled !== true;
  } catch {
    wrap.hidden = true;
  }
  button.addEventListener('click', async () => {
    button.disabled = true;
    const label = button.textContent;
    button.textContent = 'Waiting for Plex...';
    try {
      await runPlexSignIn();
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  });
}
