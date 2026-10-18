import { Badge } from '@/components/primitives';

import styles from './release-metadata.module.css';
export function ReleaseBadges({
  format,
  country,
  disambiguation,
}: {
  format?: string;
  country?: string;
  disambiguation?: string;
}) {
  const known =
    country &&
    /^[A-Z]{2}$/.test(country) &&
    !['XE', 'XW', 'XU', 'SU', 'YU', 'CS', 'DD'].includes(country);
  let countryName = country === 'XW' ? 'Worldwide' : country === 'XE' ? 'Europe' : country;
  if (known) {
    try {
      countryName = new Intl.DisplayNames(['en'], { type: 'region' }).of(country!) || country;
    } catch {}
  }
  const area = country
    ? `${known ? String.fromCodePoint(...country.split('').map((c) => c.charCodeAt(0) + 127397)) : '🌐'} ${countryName}`
    : '';
  return (
    <span className={styles.badges}>
      {[format, area, disambiguation].filter(Boolean).map((value) => (
        <Badge key={value}>{value}</Badge>
      ))}
    </span>
  );
}
