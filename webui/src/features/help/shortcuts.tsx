/**
 * Shortcuts (plan 4.8, mockup's shortcuts view), generated from shortcuts.ts,
 * the one registry. Mac keys show as ⌘ ⌥ ⇧; a screen reader hears the key
 * names; a touch only device is told these need a keyboard.
 */

import { Fragment } from 'react';

import type { ViewProps } from './view-props';

import s from './help.module.css';
import {
  SHORTCUT_GROUPS,
  isMac,
  isTouchOnly,
  keyLabel,
  shortcutsIn,
  spokenKeys,
  type Shortcut,
} from './shortcut-registry';
import k from './shortcuts.module.css';
import { Kbd, Label } from './ui';

export function ShortcutsView(_: ViewProps) {
  const mac = isMac();
  const touch = isTouchOnly();
  return (
    <>
      {touch ? (
        <p className={k.touch}>
          Shortcuts need a keyboard. With one connected, these work here too.
        </p>
      ) : null}
      {SHORTCUT_GROUPS.map((group) => {
        const list = shortcutsIn(group.id);
        if (!list.length) return null;
        return (
          <Fragment key={group.id}>
            <Label>{group.label}</Label>
            {group.note ? <p className={k.note}>{group.note}</p> : null}
            <ul className={k.group} aria-label={group.label}>
              {list.map((sc) => (
                <li key={sc.id} className={k.row} data-shortcut={sc.id}>
                  <span className={k.what}>
                    {sc.label}
                    {sc.where ? <span className={k.where}>{sc.where}</span> : null}
                  </span>
                  <Keys shortcut={sc} mac={mac} />
                </li>
              ))}
            </ul>
          </Fragment>
        );
      })}
      <p className={k.foot}>
        {mac
          ? 'Shortcuts pause while you type in a field.'
          : 'On a Mac, Ctrl is ⌘. Shortcuts pause while you type in a field.'}
      </p>
    </>
  );
}

function Keys({ shortcut, mac }: { shortcut: Shortcut; mac: boolean }) {
  return (
    <span className={k.keys}>
      <span className={k.caps} aria-hidden="true">
        {shortcut.keys.map((chord, i) => (
          <Fragment key={chord.join('+')}>
            {i > 0 && shortcut.join === 'or' ? <span className={k.or}>or</span> : null}
            {chord.map((key) => (
              <Kbd key={key}>{keyLabel(key, mac)}</Kbd>
            ))}
          </Fragment>
        ))}
      </span>
      <span className={s.srOnly}>{spokenKeys(shortcut, mac)}</span>
    </span>
  );
}
