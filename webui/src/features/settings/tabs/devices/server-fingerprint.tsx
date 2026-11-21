/**
 * The server fingerprint (PAIRING_SPEC.md 1): the first 12 characters of
 * this server's lookup ID, on System › Security. A paired app shows the
 * same, so someone can check by eye that it found this server and not
 * another.
 */

import { useEffect, useState } from 'react';

import { useNav } from '../../nav';
import { Card, Note, Row } from '../../ui';
import { getIdentity, type ServerIdentity } from './api';
import css from './devices.module.css';

/** "ab12cd34ef56" as "ab12 cd34 ef56", easier to read aloud */
export function groupFingerprint(fp: string) {
  return (fp.match(/.{1,4}/g) ?? []).join(' ');
}

export function useServerIdentity(load = true) {
  const [identity, setIdentity] = useState<ServerIdentity | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!load || identity) return;
    const ctrl = new AbortController();
    getIdentity(ctrl.signal)
      .then((r) => {
        if (!r.ok) setError(r.message);
        else if (typeof r.data.fingerprint === 'string' && r.data.fingerprint) setIdentity(r.data);
      })
      .catch(() => {});
    return () => ctrl.abort();
  }, [load, identity]);
  return { identity, error };
}

/** the card. `load` defaults to "System › Security is open" */
export function ServerFingerprint({ load }: { load?: boolean }) {
  const state = useNav();
  const open = load ?? (state.tab === 'advanced' && state.view === 'security');
  const { identity, error } = useServerIdentity(open);
  return (
    <Card
      title="Server identity"
      hint={
        <>
          From the <code>server_key</code> file next to the database. A database backup holds it
          too, so keep backups private and never run a restored copy next to the original.
        </>
      }
    >
      <Row
        id="security-server-fingerprint"
        label="Server fingerprint"
        help="A paired app shows the same 12 characters, so you can check it found this server."
      >
        {identity ? (
          <code className={css.mono} title={`Lookup ID ${identity.lookup_id}`}>
            {groupFingerprint(identity.fingerprint)}
          </code>
        ) : null}
      </Row>
      {error ? <Note>{error}</Note> : null}
    </Card>
  );
}
