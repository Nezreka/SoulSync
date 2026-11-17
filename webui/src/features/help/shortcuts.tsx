/**
 * P6 replaces this file: Shortcuts (plan 4.8), generated from shortcuts.ts,
 * the one registry the palette reads too. Until then, the help keys.
 */

import type { ViewProps } from './view-props';

import s from './help.module.css';
import { Kbd, Label, List, Row } from './ui';

export function ShortcutsView(_: ViewProps) {
  return (
    <>
      <Label>Anywhere</Label>
      <List label="Anywhere">
        <Row
          title="Open help"
          end={
            <span className={s.kbds}>
              <Kbd>?</Kbd>or<Kbd>F1</Kbd>
            </span>
          }
        />
        <Row
          title="Search help"
          end={
            <span className={s.kbds}>
              <Kbd>Ctrl</Kbd>
              <Kbd>K</Kbd>
            </span>
          }
        />
        <Row title="Close a panel or window" end={<Kbd>Esc</Kbd>} />
      </List>
    </>
  );
}
