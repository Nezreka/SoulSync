import { stubCtx } from './test-ctx';
import { Visualizer } from './visualizer';

type Frame = (ts: number) => void;

function setup() {
  const frames = new Map<number, Frame>();
  let next = 1;
  const raf = vi.fn((cb: Frame) => {
    const id = next++;
    frames.set(id, cb);
    return id;
  });
  const caf = vi.fn((id: number) => {
    frames.delete(id);
  });
  vi.stubGlobal('requestAnimationFrame', raf);
  vi.stubGlobal('cancelAnimationFrame', caf);
  const main = stubCtx();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
    () => stubCtx().ctx as unknown as ReturnType<HTMLCanvasElement['getContext']>,
  );
  const canvas = document.createElement('canvas');
  vi.spyOn(canvas, 'getContext').mockImplementation(
    () => main.ctx as unknown as ReturnType<HTMLCanvasElement['getContext']>,
  );
  Object.defineProperty(canvas, 'clientWidth', { value: 400 });
  Object.defineProperty(canvas, 'clientHeight', { value: 300 });
  // run every pending frame once, at time ts
  const step = (ts: number) => {
    const pending = [...frames.entries()];
    frames.clear();
    for (const [, cb] of pending) cb(ts);
  };
  return { raf, caf, frames, canvas, main, step };
}

function fakeAnalyser(level: () => number) {
  const an = {
    fftSize: 2048,
    frequencyBinCount: 1024,
    context: { sampleRate: 48000 },
    getByteFrequencyData: vi.fn((arr: Uint8Array) => arr.fill(level())),
    getByteTimeDomainData: vi.fn((arr: Uint8Array) => {
      for (let i = 0; i < arr.length; i++) arr[i] = 128 + Math.round(90 * Math.sin(i / 10));
    }),
  };
  return an;
}

describe('Visualizer', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('start schedules one frame, stop cancels it', () => {
    const { raf, caf, frames, canvas } = setup();
    const viz = new Visualizer(canvas, { analyser: () => null, playing: () => false });
    viz.start();
    viz.start();
    expect(raf).toHaveBeenCalledTimes(1);
    expect(viz.running).toBe(true);
    viz.stop();
    expect(caf).toHaveBeenCalledWith(1);
    expect(frames.size).toBe(0);
    expect(viz.running).toBe(false);
  });

  it('destroy cancels the loop and start after destroy does nothing', () => {
    const { raf, frames, canvas, step } = setup();
    const viz = new Visualizer(canvas, { analyser: () => null, playing: () => false });
    viz.start();
    step(16);
    step(32);
    expect(frames.size).toBe(1);
    viz.destroy();
    expect(frames.size).toBe(0);
    const calls = raf.mock.calls.length;
    viz.start();
    expect(raf.mock.calls.length).toBe(calls);
  });

  it('paints idle frames, sizes the canvas from quality, no NaN', () => {
    const { canvas, main, step } = setup();
    const viz = new Visualizer(canvas, { analyser: () => null, playing: () => false });
    viz.setQuality('lite');
    viz.setTheme('scope');
    viz.start();
    for (let i = 1; i < 10; i++) step(i * 16);
    // lite caps dpr at 1
    expect(canvas.width).toBe(400);
    expect(canvas.height).toBe(300);
    expect(main.calls.some((c) => c.name === 'stroke')).toBe(true);
    expect(main.bad).toEqual([]);
    viz.destroy();
  });

  it('reads the analyser only while playing', () => {
    const { canvas, step } = setup();
    let playing = false;
    let level = 40;
    const an = fakeAnalyser(() => level);
    const viz = new Visualizer(canvas, {
      analyser: () => an as unknown as AnalyserNode,
      playing: () => playing,
    });
    viz.start();
    step(16);
    expect(an.getByteFrequencyData).not.toHaveBeenCalled();
    playing = true;
    for (let i = 2; i < 30; i++) step(i * 16);
    expect(an.getByteFrequencyData).toHaveBeenCalled();
    level = 230;
    expect(() => step(31 * 16)).not.toThrow();
    viz.destroy();
  });

  it('crossfades on theme change, paints every theme, autocycles', () => {
    const { canvas, main, step } = setup();
    const seen: string[] = [];
    const viz = new Visualizer(canvas, {
      analyser: () => null,
      playing: () => false,
      onThemeChange: (id) => seen.push(id),
    });
    viz.setPalette({ r: 200, g: 40, b: 60 });
    viz.setArt(null);
    viz.setTitle('numb');
    viz.setEnergy(1.4);
    viz.setReducedMotion(true);
    viz.start();
    step(16);
    viz.setTheme('bloom');
    step(32);
    expect(main.calls.some((c) => c.name === 'drawImage')).toBe(true);
    viz.setTheme('nope');
    expect(viz.currentTheme).toBe('aurora');
    viz.setReducedMotion(false);
    viz.setAutocycle(1);
    for (let i = 3; i < 200; i++) step(i * 100);
    expect(seen.length).toBeGreaterThan(0);
    expect(seen).not.toContain('none');
    expect(main.bad).toEqual([]);
    viz.destroy();
  });

  it('pauses while the document is hidden', () => {
    const { canvas, main, step } = setup();
    const viz = new Visualizer(canvas, { analyser: () => null, playing: () => false });
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    viz.start();
    step(16);
    expect(main.calls.length).toBe(0);
    hidden.mockReturnValue(false);
    step(32);
    expect(main.calls.length).toBeGreaterThan(0);
    viz.destroy();
  });

  it('the off theme clears once and stops painting', () => {
    const { canvas, main, step } = setup();
    const viz = new Visualizer(canvas, { analyser: () => null, playing: () => false });
    viz.setTheme('none');
    viz.start();
    step(16);
    step(32);
    expect(main.calls.map((c) => c.name)).toEqual(['clearRect']);
    viz.destroy();
  });
});
