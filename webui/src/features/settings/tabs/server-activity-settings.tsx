/**
 * Connections > Server Activity: the upload cap the drawer's bandwidth bar
 * measures against, the plex.tv location lookup, how long the play log
 * keeps plays (and a button that empties it), and the ready-made messages
 * the Stop sheet offers. Saved through /api/server-activity/settings, each
 * change on its own. Admin only, like the drawer (#2155): a 403 draws nothing.
 */

import { useEffect, useState } from 'react';

import type { ServerActivitySettings } from '@/shell/server-activity-types';

import { clearPlays, getSettings, saveSettings } from '@/features/server-activity/api';

import { Section } from './parts';
import css from './server-activity-settings.module.css';

const TAB = 'connections';
const MAX_PRESETS = 12;

export const RETENTION = [
  { value: 0, label: 'Forever' },
  { value: 30, label: '30 days' },
  { value: 90, label: '90 days' },
  { value: 180, label: '6 months' },
  { value: 365, label: '1 year' },
  { value: 730, label: '2 years' },
];

const toast = (message: string, type: string) => window.showToast?.(message, type);

export function ServerActivitySettingsCard() {
  const [settings, setSettings] = useState<ServerActivitySettings | null>(null);
  const [hidden, setHidden] = useState(false);
  const [upload, setUpload] = useState('');
  const [draft, setDraft] = useState('');

  useEffect(() => {
    const ctrl = new AbortController();
    getSettings(ctrl.signal)
      .then((result) => {
        if (!result.ok) {
          setHidden(true);
          return;
        }
        setSettings(result.data.settings);
        setUpload(result.data.settings.upload_mbps ? String(result.data.settings.upload_mbps) : '');
      })
      .catch(() => {
        // left the page first
      });
    return () => ctrl.abort();
  }, []);

  if (hidden || !settings) return null;

  const save = async (patch: Partial<ServerActivitySettings>, done?: string) => {
    const result = await saveSettings(patch);
    if (!result.ok || !result.data.ok) {
      toast((result.ok ? result.data.error : result.error) || "Couldn't save that.", 'error');
      // put the field back to what is saved
      setUpload(settings.upload_mbps ? String(settings.upload_mbps) : '');
      return;
    }
    setSettings(result.data.settings);
    setUpload(result.data.settings.upload_mbps ? String(result.data.settings.upload_mbps) : '');
    if (done) toast(done, 'success');
  };

  const saveUpload = () => {
    const text = upload.trim();
    const current = settings.upload_mbps ? String(settings.upload_mbps) : '';
    if (text === current) return;
    void save({ upload_mbps: text === '' ? null : Number(text) });
  };

  const presets = settings.stop_presets;
  const addPreset = () => {
    const text = draft.trim();
    if (!text) return;
    if (presets.includes(text)) {
      setDraft('');
      return;
    }
    setDraft('');
    void save({ stop_presets: [...presets, text] });
  };

  const clear = async () => {
    const yes = window.showConfirmDialog
      ? await window.showConfirmDialog({
          title: 'Clear the play log?',
          message:
            "Every finished play goes, and History and Stats start again from now. Streams playing right now carry on. Plex's own history is not imported again.",
          confirmText: 'Clear play log',
          cancelText: 'Keep it',
          destructive: true,
        })
      : false;
    if (!yes) return;
    const result = await clearPlays();
    if (!result.ok || !result.data.ok) {
      toast(result.ok ? "Couldn't clear the play log." : result.error, 'error');
      return;
    }
    toast(
      result.data.removed === 1
        ? 'Cleared 1 play from the log.'
        : `Cleared ${result.data.removed.toLocaleString('en-US')} plays from the log.`,
      'success',
    );
  };

  return (
    <Section tab={TAB} title="Server Activity" hint="Live streams, the play log and stop messages">
      <div className="settings-group" data-stg={TAB} data-server-activity-settings>
        <div className="form-group">
          <label htmlFor="sa-upload-mbps">Upload speed (Mbps)</label>
          <input
            id="sa-upload-mbps"
            className="form-input"
            type="number"
            min="0"
            step="any"
            inputMode="decimal"
            placeholder="Not set"
            value={upload}
            onChange={(event) => setUpload(event.target.value)}
            onBlur={saveUpload}
            onKeyDown={(event) => {
              if (event.key === 'Enter') saveUpload();
            }}
          />
          <small className="settings-hint">
            Your internet upload. Now playing shows remote streams as a share of it, so you see when
            people away from home are close to the limit. Leave it empty for no bar.
          </small>
        </div>

        <div className="form-group">
          <label className="checkbox-label">
            <input
              type="checkbox"
              id="sa-geo-lookup"
              checked={settings.geo_lookup}
              onChange={(event) => void save({ geo_lookup: event.target.checked })}
            />{' '}
            Show where remote viewers are
          </label>
          <small className="settings-hint">
            Looks up the city of a remote address at plex.tv with your Plex token, the way Tautulli
            does, and remembers it for 30 days. Home addresses are never looked up. Off keeps every
            address on this server.
          </small>
        </div>

        <div className="form-group">
          <label htmlFor="sa-retention">Keep plays for</label>
          <select
            id="sa-retention"
            className="form-select"
            value={settings.retention_days}
            onChange={(event) => void save({ retention_days: Number(event.target.value) })}
          >
            {RETENTION.some((r) => r.value === settings.retention_days) ? null : (
              <option value={settings.retention_days}>{settings.retention_days} days</option>
            )}
            {RETENTION.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
          <small className="settings-hint">
            SoulSync's own play log, which History and Stats read. It is small, so forever is the
            default. Older plays are removed once an hour.
          </small>
          <div className="form-actions">
            <button type="button" className="test-button" onClick={() => void clear()}>
              Clear play log
            </button>
          </div>
        </div>

        <div className="form-group">
          <label htmlFor="sa-preset-new">Stop messages</label>
          <small className="settings-hint">
            Ready-made messages the Stop sheet offers. The viewer sees the one you pick when their
            stream ends.
          </small>
          <ul className={css.presets} aria-label="Stop messages">
            {presets.map((preset) => (
              <li key={preset}>
                <span>{preset}</span>
                <button
                  type="button"
                  aria-label={`Remove "${preset}"`}
                  title="Remove"
                  onClick={() => void save({ stop_presets: presets.filter((p) => p !== preset) })}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
          {presets.length < MAX_PRESETS ? (
            <div className="path-input-group">
              <input
                id="sa-preset-new"
                className="form-input"
                type="text"
                maxLength={240}
                placeholder="Add a message"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    addPreset();
                  }
                }}
              />
              <button
                type="button"
                className="test-button"
                disabled={!draft.trim()}
                onClick={addPreset}
              >
                Add
              </button>
            </div>
          ) : (
            <small className="settings-hint">That's the most it keeps ({MAX_PRESETS}).</small>
          )}
        </div>
      </div>
    </Section>
  );
}
