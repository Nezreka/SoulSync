/**
 * this screen's id: made once, kept in this browser. it lets one profile
 * arrange its home differently on the tv and the phone. without storage
 * (a private window) it lasts the visit, and the screen uses the profile's
 * shared layout.
 */

const KEY = 'ss-player-device';
let session: string | null = null;

function make(): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return `d-${Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')}`;
}

export function deviceId(): string {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved && /^[A-Za-z0-9_-]{8,64}$/.test(saved)) return saved;
    const id = make();
    localStorage.setItem(KEY, id);
    return id;
  } catch {
    session ??= make();
    return session;
  }
}
