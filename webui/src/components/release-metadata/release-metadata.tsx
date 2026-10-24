import { Tooltip } from '@base-ui/react/tooltip';
import * as flags from 'country-flag-icons/react/3x2';
import { useId, useState, type ReactNode } from 'react';

import { Badge } from '@/components/primitives';

import styles from './release-metadata.module.css';

function ReleaseBadge({ children, description }: { children: ReactNode; description: string }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <Tooltip.Root open={open} onOpenChange={setOpen}>
      <Tooltip.Trigger render={<Badge tabIndex={0} />} aria-describedby={open ? id : undefined}>
        {children}
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Positioner sideOffset={6} className={styles.tooltipPosition}>
          <Tooltip.Popup id={id} role="tooltip" className={styles.tooltip}>
            {description}
          </Tooltip.Popup>
        </Tooltip.Positioner>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

export function ReleaseBadges({
  format,
  country,
  disambiguation,
}: {
  format?: string;
  country?: string;
  disambiguation?: string;
}) {
  const code = country?.trim().toUpperCase() || '';
  // MusicBrainz also uses historical and non-country areas. Never assign them
  // a modern national flag. SVGs avoid OS-dependent emoji glyph selection.
  const Flag =
    /^[A-Z]{2}$/.test(code) && Object.hasOwn(flags, code)
      ? flags[code as keyof typeof flags]
      : null;
  let countryName = code === 'XW' ? 'Worldwide' : code === 'XE' ? 'Europe' : code;
  if (Flag) {
    try {
      countryName = new Intl.DisplayNames(['en'], { type: 'region' }).of(code) || code;
    } catch {}
  }
  return (
    <Tooltip.Provider delay={300}>
      <span className={styles.badges}>
        {format && (
          <ReleaseBadge
            description={`Release format: ${format}. The catalog edition's medium, not the local audio file format.`}
          >
            {format}
          </ReleaseBadge>
        )}
        {code && (
          <ReleaseBadge
            description={`Release country or territory: ${countryName}. Where this edition was issued.`}
          >
            {Flag ? (
              <Flag className={styles.flag} aria-hidden="true" data-country={code} />
            ) : (
              <span aria-hidden="true">🌐</span>
            )}
            {countryName}
          </ReleaseBadge>
        )}
        {disambiguation && (
          <ReleaseBadge
            description={`Edition description: ${disambiguation}. Catalog details that distinguish this release.`}
          >
            {disambiguation}
          </ReleaseBadge>
        )}
      </span>
    </Tooltip.Provider>
  );
}
