/**
 * Settings > System > Backups: every database SoulSync keeps, in one place.
 *
 * Music and video each had a card on the Tools page and nothing else did.
 * This is the whole picture: the music database, the player's and the
 * audiobooks' (which ride along with every music backup), the video
 * database, and the key every saved credential is encrypted with. Each can
 * be backed up, downloaded and restored from here.
 *
 * It loads when the pill is first opened, and again on each arrival.
 */

import { useCallback, useEffect, useState } from 'react';

import type { BackupListResponse } from '@/routes/tools/-tools.types';

import {
  backupDownloadUrl,
  createBackup,
  deleteBackup,
  fetchBackups,
  restoreBackup,
} from '@/routes/tools/-tools.api';
import {
  cancelVideoRestore,
  createVideoBackup,
  fetchVideoBackups,
  stageVideoRestore,
  videoBackupDownloadUrl,
  type VideoBackupList,
} from '@/routes/tools/-video-tools.api';

import { Btn, Card, Note, Row } from '../../ui';
import s from '../../ui/settings.module.css';

interface CompanionBackup {
  filename: string;
  size_bytes: number;
  modified: number;
}
interface Companion {
  name: string;
  label: string;
  about: string;
  size_bytes: number;
  backups: CompanionBackup[];
}
interface CompanionList {
  success?: boolean;
  companions?: Companion[];
  key_file?: boolean;
}

const toast = (message: string, type = 'info') => window.showToast?.(message, type);

/** "music_library.db.backup_20261125_043000" -> "25 Nov 2026, 04:30" */
export function stampOf(filename: string): string {
  const m = /(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/.exec(filename);
  if (!m) return filename;
  const when = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
  return when.toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function sizeOf(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

async function confirm(title: string, message: string, confirmText: string) {
  return (await window.showConfirmDialog?.({ title, message, confirmText })) ?? false;
}

interface DbHealth {
  healthy: boolean | null;
  detail: string;
  checked_at: number | null;
  checking: boolean;
  repair_tool: boolean;
}

interface RepairAnswer {
  success?: boolean;
  healthy?: boolean;
  repaired?: boolean;
  message?: string;
  error?: string;
  kept?: string;
  backup?: string | null;
}

/** when it was checked, in words */
export function checkedWhen(at: number | null, now = Date.now()): string {
  if (!at) return 'Not checked yet';
  const mins = Math.floor((now - at * 1000) / 60000);
  if (mins < 1) return 'Checked just now';
  if (mins < 60) return `Checked ${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `Checked ${hours} h ago`;
  return `Checked ${new Date(at * 1000).toLocaleDateString()}`;
}

/**
 * the music database's health, at the top of the page. a damaged database
 * stops every backup (they refuse to copy damage), so this says so loudly and
 * offers the repair: the damaged files are kept first, then a repaired copy is
 * checked and swapped in. when it can't be repaired, the newest good backup.
 */
function DatabaseHealth({
  shown,
  onRestore,
}: {
  shown: boolean;
  onRestore: (filename: string) => void;
}) {
  const [health, setHealth] = useState<DbHealth | null>(null);
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<RepairAnswer | null>(null);

  const read = useCallback(async (check = false) => {
    try {
      const res = await fetch(`/api/database/health${check ? '?check=1' : ''}`);
      const data = (await res.json()) as DbHealth & { success?: boolean };
      if (data.success) setHealth(data);
    } catch {
      setHealth(null);
    }
  }, []);

  useEffect(() => {
    if (!shown) return;
    void read();
  }, [shown, read]);

  // a check runs in the background: ask again until it has answered
  useEffect(() => {
    if (!shown || !health?.checking) return;
    const t = window.setTimeout(() => void read(), 3000);
    return () => window.clearTimeout(t);
  }, [shown, health, read]);

  const repair = async () => {
    if (
      !(await confirm(
        'Repair the music database',
        'The damaged files are kept first, in a folder of their own beside the database. Then a repaired copy is made, checked, and swapped in. This can take a few minutes on a large library, and SoulSync is busy while it runs.',
        'Repair',
      ))
    )
      return;
    setBusy(true);
    setAnswer(null);
    try {
      const res = await fetch('/api/database/repair', { method: 'POST' });
      const data = (await res.json()) as RepairAnswer;
      setAnswer(data);
      if (data.repaired) toast('Database repaired. Restart SoulSync to finish.', 'success');
      else if (data.healthy) toast('Your database is healthy. Nothing to repair.', 'info');
      else toast(`Not repaired: ${data.error ?? 'unknown error'}`, 'error');
    } catch {
      toast('The repair did not answer. Check the logs, then try again.', 'error');
    } finally {
      setBusy(false);
      await read();
    }
  };

  const damaged = health?.healthy === false;
  return (
    <Card
      title="Database health"
      danger={damaged}
      hint={
        damaged
          ? 'Your music database is damaged. Backups have stopped, because a backup never copies damage.'
          : 'Every backup checks the database first. You can check it now too.'
      }
      actions={
        damaged ? (
          <Btn kind="primary" small busy={busy} onClick={() => void repair()}>
            Repair
          </Btn>
        ) : (
          <Btn small busy={health?.checking} onClick={() => void read(true)}>
            Check now
          </Btn>
        )
      }
    >
      <Row
        label={
          health?.checking
            ? 'Checking…'
            : damaged
              ? 'Damaged'
              : health?.healthy
                ? 'Healthy'
                : 'Not checked yet'
        }
        help={health ? checkedWhen(health.checked_at) : undefined}
      />
      {damaged && health?.detail ? <Note>{health.detail}</Note> : null}
      {answer?.repaired ? (
        <Note info>
          {answer.message} The damaged files are kept in {answer.kept}. Restart SoulSync to finish.
        </Note>
      ) : null}
      {answer && answer.success === false && !answer.repaired ? (
        <Note
          action={
            answer.backup ? (
              <Btn small onClick={() => onRestore(answer.backup!)}>
                Restore {stampOf(answer.backup)}
              </Btn>
            ) : undefined
          }
        >
          {answer.error}
          {answer.kept ? ` The damaged files are kept in ${answer.kept}.` : ''}
          {answer.backup
            ? ' The newest healthy backup can be restored instead.'
            : ' There is no healthy backup to restore.'}
        </Note>
      ) : null}
    </Card>
  );
}

export function BackupsView({ shown, opened }: { shown: boolean; opened: number }) {
  const [music, setMusic] = useState<BackupListResponse | null>(null);
  const [others, setOthers] = useState<CompanionList | null>(null);
  const [video, setVideo] = useState<VideoBackupList | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [m, v] = await Promise.all([fetchBackups(), fetchVideoBackups()]);
    setMusic(m);
    setVideo(v);
    try {
      const data = (await (await fetch('/api/database/companions')).json()) as CompanionList;
      setOthers(data.success ? data : null);
    } catch {
      setOthers(null);
    }
  }, []);

  useEffect(() => {
    if (shown) void load();
  }, [shown, opened, load]);

  const run = async (what: string, job: () => Promise<void>) => {
    setBusy(what);
    try {
      await job();
    } catch {
      toast('That did not go through. Try again in a moment.', 'error');
    } finally {
      setBusy(null);
      await load();
    }
  };

  const backupMusic = () =>
    run('music', async () => {
      const result = await createBackup();
      if (result.success) toast(`Backed up (${result.size_mb} MB)`, 'success');
      else toast(`Backup failed: ${result.error}`, 'error');
    });

  const restoreMusic = async (filename: string, force = false) => {
    if (
      !force &&
      !(await confirm(
        'Restore the music database',
        `Put the music database back to ${stampOf(filename)}?\n\nThe backup is checked first, and a copy of the database as it is now is kept before anything is replaced.`,
        'Restore',
      ))
    )
      return;
    await run(`restore:${filename}`, async () => {
      const result = await restoreBackup(filename, force);
      if (result.success) toast('Restored. Restart SoulSync to finish.', 'success');
      else if (result.version_mismatch) {
        if (
          await confirm(
            'A different version',
            `${result.error}\n\nRestore it anyway?`,
            'Restore anyway',
          )
        )
          await restoreMusic(filename, true);
      } else toast(`Not restored: ${result.error}`, 'error');
    });
  };

  const removeMusic = async (filename: string) => {
    if (!(await confirm('Delete backup', `Delete the backup from ${stampOf(filename)}?`, 'Delete')))
      return;
    await run(`delete:${filename}`, async () => {
      const result = await deleteBackup(filename);
      if (!result.success) toast(`Not deleted: ${result.error}`, 'error');
    });
  };

  const restoreOther = async (c: Companion, filename: string) => {
    if (
      !(await confirm(
        `Restore the ${c.label.toLowerCase()} database`,
        `Put it back to ${stampOf(filename)}?\n\nA copy of the database as it is now is kept first.`,
        'Restore',
      ))
    )
      return;
    await run(`restore:${filename}`, async () => {
      const response = await fetch(
        `/api/database/companions/${encodeURIComponent(c.name)}/restore`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filename }),
        },
      );
      const result = (await response.json()) as { success?: boolean; error?: string };
      if (result.success) toast(`${c.label} database restored`, 'success');
      else toast(`Not restored: ${result.error}`, 'error');
    });
  };

  const backupVideo = () =>
    run('video', async () => {
      const result = await createVideoBackup();
      if (result.success) toast('Video database backed up', 'success');
      else toast(`Backup failed: ${result.error}`, 'error');
    });

  const stageVideo = async (name: string) => {
    if (
      !(await confirm(
        'Restore the video database',
        `Put the video database back to ${stampOf(name)}?\n\nIt is swapped in the next time SoulSync starts, and the database as it is now is kept.`,
        'Stage restore',
      ))
    )
      return;
    await run(`restore:${name}`, async () => {
      const result = await stageVideoRestore(name);
      if (result.success) toast('Staged. Restart SoulSync to apply it.', 'success');
      else toast(`Not staged: ${result.error}`, 'error');
    });
  };

  return (
    <div className={s.cards}>
      <DatabaseHealth shown={shown} onRestore={(name) => void restoreMusic(name)} />
      <Card
        title="Music"
        hint="The library, playlists, wishlist, listening history and every setting. The newest five are kept."
        actions={
          <Btn kind="primary" small busy={busy === 'music'} onClick={() => void backupMusic()}>
            Back up now
          </Btn>
        }
      >
        {music?.backups.length ? (
          music.backups.map((b) => (
            <Row
              key={b.filename}
              label={stampOf(b.filename)}
              help={`${b.size_mb} MB${b.version ? ` · v${b.version.split('+')[0]}` : ''}`}
            >
              <a className={`${s.btn} ${s.btnSm}`} href={backupDownloadUrl(b.filename)}>
                Download
              </a>
              <Btn
                small
                busy={busy === `restore:${b.filename}`}
                onClick={() => void restoreMusic(b.filename)}
              >
                Restore
              </Btn>
              <Btn
                kind="danger"
                small
                busy={busy === `delete:${b.filename}`}
                onClick={() => void removeMusic(b.filename)}
              >
                Delete
              </Btn>
            </Row>
          ))
        ) : (
          <div className={s.listEmpty}>No backups yet.</div>
        )}
      </Card>

      {(others?.companions ?? []).map((c) => (
        <Card
          key={c.name}
          title={c.label}
          hint={`${c.about} Backed up with every music backup (${sizeOf(c.size_bytes)}).`}
        >
          {c.backups.length ? (
            c.backups.map((b) => (
              <Row key={b.filename} label={stampOf(b.filename)} help={sizeOf(b.size_bytes)}>
                <Btn
                  small
                  busy={busy === `restore:${b.filename}`}
                  onClick={() => void restoreOther(c, b.filename)}
                >
                  Restore
                </Btn>
              </Row>
            ))
          ) : (
            <div className={s.listEmpty}>
              No backups yet. The next music backup makes the first one.
            </div>
          )}
        </Card>
      ))}

      <Card
        title="Video"
        hint="Movies, shows, the video wishlist and download history. A restore is applied the next time SoulSync starts."
        actions={
          <Btn kind="primary" small busy={busy === 'video'} onClick={() => void backupVideo()}>
            Back up now
          </Btn>
        }
      >
        {video?.pending_restore ? (
          <Note>
            A restore is staged and will be applied at the next start.{' '}
            <Btn small onClick={() => void run('cancel', cancelVideoRestore)}>
              Cancel it
            </Btn>
          </Note>
        ) : null}
        {video?.backups.length ? (
          video.backups.map((b) => (
            <Row key={b.name} label={stampOf(b.name)} help={sizeOf(b.size_bytes)}>
              <a className={`${s.btn} ${s.btnSm}`} href={videoBackupDownloadUrl(b.name)}>
                Download
              </a>
              <Btn
                small
                busy={busy === `restore:${b.name}`}
                onClick={() => void stageVideo(b.name)}
              >
                Restore
              </Btn>
            </Row>
          ))
        ) : (
          <div className={s.listEmpty}>No backups yet.</div>
        )}
      </Card>

      <Card
        title="Encryption key"
        hint="Every login and API key saved in Settings is encrypted with this key. A backup restored on another computer needs it, or those have to be entered again."
      >
        {others?.key_file ? (
          <Row
            label="Keep a copy with your backups"
            help="A copy is also kept beside the database with every music backup."
          >
            <a className={`${s.btn} ${s.btnSm}`} href="/api/database/encryption-key/download">
              Download the key
            </a>
          </Row>
        ) : (
          <div className={s.listEmpty}>
            There is no key yet. It is made the first time a credential is saved.
          </div>
        )}
        <Note info>
          Treat it like a password: anyone with the key and a backup can read the credentials in it.
        </Note>
      </Card>
    </div>
  );
}
