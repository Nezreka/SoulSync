import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';
import { Button, RangeInput, Select, Switch } from '@/components/form/form';

import {
  autoImportSettingsQueryOptions,
  invalidateAutoImportQueries,
  qualityProfilesQueryOptions,
  saveAutoImportSettings,
} from '../-import.api';
import styles from './import-page.module.css';
import { getErrorMessage } from './import-shared';

/**
 * The worker's knobs. They used to sit inline above the results and get
 * scrolled past every visit; here they are read once and saved once.
 */
export function SettingsDrawer({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const settings = useQuery({ ...autoImportSettingsQueryOptions(), enabled: open });
  const profiles = useQuery({ ...qualityProfilesQueryOptions(), enabled: open });

  const [confidence, setConfidence] = useState(90);
  const [interval, setInterval] = useState(60);
  const [autoProcess, setAutoProcess] = useState(true);
  const [profileId, setProfileId] = useState<number | null>(null);

  useEffect(() => {
    const data = settings.data;
    if (!data) return;
    setConfidence(Math.round((data.confidence_threshold ?? 0.9) * 100));
    setInterval(data.scan_interval ?? 60);
    setAutoProcess(data.auto_process ?? true);
    setProfileId(data.quality_profile_id ?? null);
  }, [settings.data]);

  const save = useMutation({
    mutationFn: () =>
      saveAutoImportSettings({
        confidenceThreshold: confidence / 100,
        scanInterval: interval,
        qualityProfileId: profileId,
        autoProcess,
      }),
    onSuccess: () => {
      window.showToast?.('Settings saved', 'success');
      void invalidateAutoImportQueries(queryClient);
      onOpenChange(false);
    },
    onError: (err) => window.showToast?.(getErrorMessage(err), 'error'),
  });

  return (
    <DialogFrame open={open} onOpenChange={onOpenChange}>
      <DialogHeader title="Auto-import settings">
        How the watcher decides what to import on its own.
      </DialogHeader>
      <DialogBody>
        <div className={styles.settingsGrid}>
          <div className={styles.settingRow}>
            <div>
              <div className={styles.settingLabel}>Import without asking</div>
              <div className={styles.settingHelp}>
                Matches above the confidence line go straight into the library. Off, everything
                waits for you in Needs attention.
              </div>
            </div>
            <div className={styles.settingControl}>
              <Switch
                checked={autoProcess}
                aria-label="Import without asking"
                onCheckedChange={(checked) => setAutoProcess(Boolean(checked))}
              />
            </div>
          </div>

          <div className={styles.settingRow}>
            <div>
              <div className={styles.settingLabel}>Confidence line</div>
              <div className={styles.settingHelp}>
                Above this, an album imports on its own. Between 70% and this, it waits for review.
                Below 70%, it needs identifying.
              </div>
            </div>
            <div className={styles.settingControl}>
              <RangeInput
                label="Confidence"
                min={70}
                max={100}
                value={confidence}
                onValueChange={setConfidence}
              />
              <span className={styles.settingValue} id="auto-import-conf-val">
                {confidence}%
              </span>
            </div>
          </div>

          <div className={styles.settingRow}>
            <div>
              <div className={styles.settingLabel}>Check the folder every</div>
              <div className={styles.settingHelp}>
                A folder is only picked up once its files have stopped changing.
              </div>
            </div>
            <div className={styles.settingControl}>
              <Select
                id="auto-import-interval"
                size="sm"
                value={interval}
                onChange={(event) => setInterval(Number(event.target.value))}
              >
                <option value="30">30 seconds</option>
                <option value="60">minute</option>
                <option value="120">2 minutes</option>
                <option value="300">5 minutes</option>
                <option value="900">15 minutes</option>
              </Select>
            </div>
          </div>

          <div className={styles.settingRow}>
            <div>
              <div className={styles.settingLabel}>Quality profile</div>
              <div className={styles.settingHelp}>
                Which rules imported files are checked against. Leave on the app-wide default unless
                auto-import should be stricter or looser than downloads.
              </div>
            </div>
            <div className={styles.settingControl}>
              <Select
                id="auto-import-quality-profile"
                size="sm"
                disabled={!settings.data}
                value={profileId ?? ''}
                onChange={(event) =>
                  setProfileId(event.target.value ? Number(event.target.value) : null)
                }
              >
                <option value="">App-wide default</option>
                {(profiles.data ?? []).map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                    {profile.is_default ? ' (current default)' : ''}
                  </option>
                ))}
              </Select>
            </div>
          </div>
        </div>
      </DialogBody>
      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button
          variant="primary"
          disabled={save.isPending || !settings.data}
          onClick={() => save.mutate()}
        >
          Save
        </Button>
      </DialogFooter>
    </DialogFrame>
  );
}
