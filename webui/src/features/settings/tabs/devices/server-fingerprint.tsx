/**
 * The server fingerprint (PAIRING_SPEC.md 1): the first 12 characters of
 * this server's lookup ID. A paired app shows the same, so someone can
 * check by eye that it found this server and not another.
 */

import { useEffect, useState } from 'react';

import { getIdentity, type ServerIdentity } from './api';

/** "ab12cd34ef56" as "ab12 cd34 ef56", easier to read aloud */
export function groupFingerprint(fp: string) {
  return (fp.match(/.{1,4}/g) ?? []).join(' ');
}

export function useServerIdentity(load = true) {
  const [identity, setIdentity] = useState<ServerIdentity | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!load) return;
    const ctrl = new AbortController();
    getIdentity(ctrl.signal)
      .then((r) => {
        if (!r.ok) setError(r.message);
        else if (typeof r.data.fingerprint === 'string' && r.data.fingerprint) setIdentity(r.data);
      })
      .catch(() => {});
    return () => ctrl.abort();
  }, [load]);
  return { identity, error };
}

export function ServerFingerprint({ load = true }: { load?: boolean }) {
  const { identity, error } = useServerIdentity(load);
  return (
    <div className="form-group" id="security-server-fingerprint">
      <label>Server fingerprint:</label>
      {identity ? (
        <code title={`Lookup ID ${identity.lookup_id}`}>
          {groupFingerprint(identity.fingerprint)}
        </code>
      ) : error ? (
        <span className="setting-help-text" role="alert">
          {error}
        </span>
      ) : null}
      <div className="setting-help-text">
        This server's own ID. A paired app shows the same 12 characters, so you can check it found
        this server. It comes from the <code>server_key</code> file next to the database; a database
        backup holds it too, so keep backups private and never run a restored copy next to the
        original.
      </div>
    </div>
  );
}
