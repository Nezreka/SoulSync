import { ambientColorsFromImage, extractAmbientColors, hue, punchUpColor } from './color';

function pixels(fill: (i: number) => [number, number, number, number], n = 64 * 64) {
  const data = new Uint8ClampedArray(n * 4);
  for (let i = 0; i < n; i++) data.set(fill(i), i * 4);
  return data;
}

function hueDist(a: number, b: number): number {
  const d = Math.abs(a - b);
  return Math.min(d, 360 - d);
}

describe('ambient colour', () => {
  it('picks the saturated colour over a bigger dull background', () => {
    // 70% dull grey-brown, 20% vivid red, 10% vivid blue
    const data = pixels((i) => {
      // the extractor samples every 4th pixel
      const k = (i >> 2) % 10;
      if (k < 7) return [110, 100, 90, 255];
      if (k < 9) return [220, 30, 40, 255];
      return [30, 60, 230, 255];
    });
    const out = extractAmbientColors(data);
    expect(out).not.toBeNull();
    if (!out) return;
    const h = hue(out.main.r, out.main.g, out.main.b);
    expect(hueDist(h, hue(220, 30, 40))).toBeLessThan(10);
    const h2 = hue(out.accent.r, out.accent.g, out.accent.b);
    expect(hueDist(h, h2)).toBeGreaterThanOrEqual(40);
  });

  it('gives a single-colour cover a close neighbour as accent', () => {
    const out = extractAmbientColors(pixels(() => [40, 180, 90, 255]));
    expect(out).not.toBeNull();
    if (!out) return;
    expect(out.accent).not.toEqual(out.main);
    for (const v of [out.accent.r, out.accent.g, out.accent.b]) {
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(255);
    }
  });

  it('returns null when nothing qualifies', () => {
    expect(extractAmbientColors(pixels(() => [0, 0, 0, 255]))).toBeNull();
    expect(extractAmbientColors(pixels(() => [255, 255, 255, 255]))).toBeNull();
    expect(extractAmbientColors(pixels(() => [200, 10, 10, 0]))).toBeNull();
  });

  it('punch-up keeps greys and floors dark colours', () => {
    expect(punchUpColor(90, 90, 90)).toEqual([90, 90, 90]);
    const [r, g, b] = punchUpColor(30, 10, 10);
    expect((r + g + b) / 3).toBeGreaterThanOrEqual(69);
    expect(r).toBeGreaterThan(g);
  });

  it('image helper returns null on a tainted canvas', () => {
    const img = document.createElement('img');
    Object.defineProperty(img, 'complete', { value: true });
    Object.defineProperty(img, 'naturalWidth', { value: 300 });
    const ctx = {
      drawImage: () => {},
      getImageData: () => {
        throw new DOMException('tainted', 'SecurityError');
      },
    };
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
      () => ctx as unknown as ReturnType<HTMLCanvasElement['getContext']>,
    );
    expect(ambientColorsFromImage(img)).toBeNull();
  });

  it('image helper reads the pixels it drew', () => {
    const img = document.createElement('img');
    Object.defineProperty(img, 'complete', { value: true });
    Object.defineProperty(img, 'naturalWidth', { value: 300 });
    const data = pixels(() => [30, 60, 230, 255]);
    const ctx = { drawImage: () => {}, getImageData: () => ({ data }) };
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
      () => ctx as unknown as ReturnType<HTMLCanvasElement['getContext']>,
    );
    const out = ambientColorsFromImage(img);
    expect(out && out.main.b).toBeGreaterThan(200);
  });
});
