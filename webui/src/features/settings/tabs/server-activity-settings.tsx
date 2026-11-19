/**
 * System > Server Activity (its own pill since settings-bic phase 3; it was
 * under the media server on Connections): the upload cap the drawer's bandwidth bar
 * measures against, the plex.tv location lookup, how long the play log
 * keeps plays (and a button that empties it), and the ready-made messages
 * the Stop sheet offers. Saved through /api/server-activity/settings, each
 * change on its own. Admin only, like the drawer (#2155): a 403 draws nothing.
 */

import { type ReactNode, useEffect, useState } from 'react';

import type { ServerActivitySettings } from '@/shell/server-activity-types';

import { clearPlays, getSettings, saveSettings } from '@/features/server-activity/api';

import {
  Btn,
  Card,
  DangerRow,
  FieldLine,
  itemClass,
  Note,
  NumberField,
  Row,
  SectionLabel,
  Select,
  Switch,
  TextField,
} from '../ui';
import s from '../ui/settings.module.css';
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

export function ServerActivitySettingsCard({ denied }: { denied?: ReactNode } = {}) {
  const [settings, setSettings] = useState<ServerActivitySettings | null>(null);
  const [hidden, setHidden] = useState(false);
  const [failed, setFailed] = useState(false);
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
        // left the page first, or the server never answered
        if (!ctrl.signal.aborted) setFailed(true);
      });
    return () => ctrl.abort();
  }, []);

  if (hidden) return denied ?? null;
  if (failed)
    return (
      <Note>Server Activity's settings did not load. Open this page again to try once more.</Note>
    );
  if (!settings) return null;

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
    <div data-server-activity-settings="" data-ui="">
      <SectionLabel title="Now playing" hint="What the drawer measures and shows" />
      <Card>
        <Row
          label="Upload speed"
          htmlFor="sa-upload-mbps"
          field
          help="Your internet upload. Now playing shows remote streams as a share of it, so you see when people away from home are close to the limit. Leave it empty for no bar."
        >
          <NumberField
            id="sa-upload-mbps"
            min="0"
            step="any"
            inputMode="decimal"
            placeholder="Not set"
            unit="Mbps"
            value={upload}
            onChange={(event) => setUpload(event.target.value)}
            onBlur={saveUpload}
            onKeyDown={(event) => {
              if (event.key === 'Enter') saveUpload();
            }}
          />
        </Row>
        <Row
          label="Show where remote viewers are"
          htmlFor="sa-geo-lookup"
          help="Looks up the city of a remote address at plex.tv with your Plex token, the way Tautulli does, and remembers it for 30 days. Home addresses are never looked up. Off keeps every address on this server."
        >
          <Switch
            id="sa-geo-lookup"
            checked={settings.geo_lookup}
            onChange={(event) => void save({ geo_lookup: event.target.checked })}
          />
        </Row>
      </Card>

      <SectionLabel title="Play log" hint="SoulSync's own history, which History and Stats read" />
      <Card>
        <Row
          label="Keep plays for"
          htmlFor="sa-retention"
          field
          help="It is small, so forever is the default. Older plays are removed once an hour."
        >
          <Select
            id="sa-retention"
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
          </Select>
        </Row>
        <DangerRow
          label="Clear the play log"
          help="Every finished play goes; History and Stats start again from now."
          action="Clear play log"
          onAction={() => void clear()}
        />
      </Card>

      <SectionLabel
        title="Stop messages"
        hint="Ready-made messages the Stop sheet offers. The viewer sees the one you pick when their stream ends."
      />
      <Card>
        <div className={s.cardPad}>
          {presets.length ? (
            <ul className={s.items} aria-label="Stop messages">
              {presets.map((preset) => (
                <li key={preset} className={s.item}>
                  <span className={itemClass.main}>{preset}</span>
                  <Btn
                    kind="ghost"
                    small
                    iconOnly
                    icon="x"
                    aria-label={`Remove "${preset}"`}
                    title="Remove"
                    onClick={() => void save({ stop_presets: presets.filter((p) => p !== preset) })}
                  />
                </li>
              ))}
            </ul>
          ) : (
            <p className={s.listEmpty}>No messages yet.</p>
          )}
          {presets.length < MAX_PRESETS ? (
            <FieldLine>
              <TextField
                full
                id="sa-preset-new"
                aria-label="A new stop message"
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
              <Btn small icon="plus" disabled={!draft.trim()} onClick={addPreset}>
                Add
              </Btn>
            </FieldLine>
          ) : (
            <p className={s.listEmpty}>That's the most it keeps ({MAX_PRESETS}).</p>
          )}
        </div>
      </Card>
    </div>
  );
}
