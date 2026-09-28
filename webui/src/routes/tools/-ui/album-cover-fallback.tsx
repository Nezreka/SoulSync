export interface VinylTheme {
  bg: string;
  labelBg: string;
  ringColor: string;
  text: string;
}

export function getAlbumVinylTheme(name: string): VinylTheme {
  const themes: VinylTheme[] = [
    {
      bg: 'radial-gradient(circle at 35% 35%, #2e1065 0%, #1e1b4b 55%, #090a10 100%)',
      labelBg: '#4c1d95',
      ringColor: 'rgba(192, 132, 252, 0.25)',
      text: '#e9d5ff',
    },
    {
      bg: 'radial-gradient(circle at 35% 35%, #0f172a 0%, #030712 55%, #020617 100%)',
      labelBg: '#1e293b',
      ringColor: 'rgba(148, 163, 184, 0.25)',
      text: '#cbd5e1',
    },
    {
      bg: 'radial-gradient(circle at 35% 35%, #1e3a8a 0%, #0f172a 55%, #020617 100%)',
      labelBg: '#1d4ed8',
      ringColor: 'rgba(96, 165, 250, 0.25)',
      text: '#bfdbfe',
    },
    {
      bg: 'radial-gradient(circle at 35% 35%, #064e3b 0%, #022c22 55%, #02120e 100%)',
      labelBg: '#047857',
      ringColor: 'rgba(52, 211, 153, 0.25)',
      text: '#a7f3d0',
    },
    {
      bg: 'radial-gradient(circle at 35% 35%, #701a75 0%, #4a044e 55%, #120211 100%)',
      labelBg: '#86198f',
      ringColor: 'rgba(244, 114, 182, 0.25)',
      text: '#fbcfe8',
    },
    {
      bg: 'radial-gradient(circle at 35% 35%, #7c2d12 0%, #431407 55%, #140502 100%)',
      labelBg: '#c2410c',
      ringColor: 'rgba(251, 146, 60, 0.25)',
      text: '#fed7aa',
    },
    {
      bg: 'radial-gradient(circle at 35% 35%, #134e4a 0%, #042f2e 55%, #02100f 100%)',
      labelBg: '#0f766e',
      ringColor: 'rgba(45, 212, 191, 0.25)',
      text: '#99f6e4',
    },
    {
      bg: 'radial-gradient(circle at 35% 35%, #831843 0%, #500724 55%, #14020a 100%)',
      labelBg: '#9f1239',
      ringColor: 'rgba(251, 113, 133, 0.25)',
      text: '#fecdd3',
    },
  ];

  let hash = 0;
  for (let i = 0; i < (name || '').length; i++) {
    hash = (hash << 5) - hash + name.charCodeAt(i);
    hash |= 0;
  }
  return themes[Math.abs(hash) % themes.length];
}

export function VinylCoverFallback({ name, initialChar }: { name: string; initialChar: string }) {
  const theme = getAlbumVinylTheme(name);

  return (
    <div className="repair-vinyl-disc" style={{ background: theme.bg }} aria-hidden="true">
      <div className="repair-vinyl-grooves" />
      <div className="repair-vinyl-sheen" />
      <div
        className="repair-vinyl-label"
        style={{
          background: theme.labelBg,
          borderColor: theme.ringColor,
        }}
      >
        <span className="repair-album-art-fallback" style={{ color: theme.text }}>
          {initialChar}
        </span>
      </div>
    </div>
  );
}
