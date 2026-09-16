import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
const runtime = readFileSync('static/url-base.js', 'utf8');
for (const base of ['', '/soulsync', '/media/soulsync']) {
  test(`URL boundary preserves requests and navigation at ${base || '/'}`, async ({page}) => {
    const seen: string[] = [];
    await page.route('http://soulsync.test/**', async route => {
      const path = new URL(route.request().url()).pathname;
      seen.push(path);
      if (path === base + '/discover') {
        await route.fulfill({contentType: 'text/html', body: `<meta name="soulsync-url-base" content="${base}"><script>const nativeFetch = window.fetch; window.fetch = async (input, init) => { if (input instanceof Request) window.sentRequest = {body: await input.clone().text(), method: input.method, header: input.headers.get('X-Test')}; return nativeFetch(input, init); };</script><script>${runtime}</script><main></main>`});
      } else await route.fulfill({contentType: 'application/json', body: JSON.stringify({ok:true, body:route.request().postData()})});
    });
    await page.goto('http://soulsync.test' + base + '/discover');
    const result = await page.evaluate(async () => {
      const api = (window as any).SoulSyncURL;
      await fetch('/api/one');
      const response = await fetch(new Request('/api/two', {method:'POST', body:'payload', headers:{'X-Test':'yes'}}));
      await fetch(new URL('/api/three', location.origin));
      await new Promise<void>(resolve => {const xhr = new XMLHttpRequest(); xhr.open('GET', '/api/four'); xhr.onload = () => resolve(); xhr.send();});
      const img = document.createElement('img'); img.src = '/api/image-one'; document.body.append(img);
      const second = document.createElement('img'); second.setAttribute('src', '/api/image-two'); document.body.append(second);
      document.querySelector('main')!.innerHTML = '<img src="/api/image-three"><a href="/library">Library</a>';
      const audio = document.createElement('audio'); audio.src = '/stream/audio?id=1';
      document.querySelector('main')!.style.backgroundImage = `url('${api.resolve('/api/backdrop')}')`;
      const backdrop = document.querySelector('main')!.style.backgroundImage;
      const external = api.resolve('https://example.org/music');
      history.pushState({}, '', '/library');
      return {posted: (window as any).sentRequest, path: location.pathname, logical: api.strip(location.pathname),
              image: img.getAttribute('src'), markup: document.querySelector('main img')!.getAttribute('src'),
              backdrop, audio: audio.getAttribute('src'), external, twice: api.resolve(api.resolve('/api/one'))};
    });
    expect(result.posted).toEqual({body:'payload', method:'POST', header:'yes'});
    expect(result.path).toBe(base + '/library');
    expect(result.logical).toBe('/library');
    expect(result.image).toBe(base + '/api/image-one');
    expect(result.markup).toBe(base + '/api/image-three');
    expect(result.audio).toBe(base + '/stream/audio?id=1');
    expect(result.backdrop).toContain(base + '/api/backdrop');
    expect(result.external).toBe('https://example.org/music');
    expect(result.twice).toBe(base + '/api/one');
    for (const endpoint of ['one','two','three','four']) expect(seen).toContain(base + '/api/' + endpoint);
    await expect.poll(() => seen.includes(base + '/api/image-three')).toBe(true);
    if (base) expect(seen.filter(path => !path.startsWith(base + '/'))).toEqual([]);
  });
}
