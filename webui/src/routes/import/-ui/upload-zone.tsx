import { useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { type DragEvent, type ReactNode, useEffect, useRef, useState } from 'react';

import { Button } from '@/components/form/form';

import { invalidateImportStagingQueries, uploadImportFile } from '../-import.api';
import { formatImportBytes } from '../-import.helpers';
import styles from './import-page.module.css';
import { getErrorMessage } from './import-shared';

/** a file the browser handed us, with the folder path it came with */
interface Picked {
  file: File;
  path: string;
}

interface UploadRow {
  id: number;
  path: string;
  size: number;
  fraction: number;
  status: 'queued' | 'uploading' | 'done' | 'skipped' | 'error';
  note?: string;
}

const AUDIO = new Set([
  '.mp3',
  '.flac',
  '.ogg',
  '.opus',
  '.m4a',
  '.aac',
  '.wav',
  '.wma',
  '.aiff',
  '.aif',
  '.ape',
]);

function isAudio(name: string): boolean {
  const dot = name.lastIndexOf('.');
  return dot >= 0 && AUDIO.has(name.slice(dot).toLowerCase());
}

/**
 * Walk a dropped DataTransfer. A dropped folder comes as a directory entry;
 * webkitGetAsEntry is the only way to read inside it, and it is what every
 * browser that supports dropping folders ships.
 */
async function collectDropped(items: DataTransferItemList): Promise<Picked[]> {
  const out: Picked[] = [];
  const entries: FileSystemEntry[] = [];
  for (const item of Array.from(items)) {
    const entry = (
      item as DataTransferItem & { webkitGetAsEntry?: () => FileSystemEntry | null }
    ).webkitGetAsEntry?.();
    if (entry) entries.push(entry);
  }
  if (entries.length === 0) return out;

  const readEntry = async (entry: FileSystemEntry, prefix: string): Promise<void> => {
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) =>
        (entry as FileSystemFileEntry).file(resolve, reject),
      );
      out.push({ file, path: prefix + file.name });
      return;
    }
    if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      // readEntries returns in batches; keep reading until it hands back nothing
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
          reader.readEntries(resolve, reject),
        );
        if (batch.length === 0) break;
        for (const child of batch) await readEntry(child, `${prefix}${entry.name}/`);
      }
    }
  };
  for (const entry of entries) await readEntry(entry, '');
  return out;
}

function fromInput(list: FileList | null): Picked[] {
  if (!list) return [];
  return Array.from(list).map((file) => ({
    file,
    path: (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name,
  }));
}

/**
 * Drop files or folders onto the inbox and they go into the import folder,
 * folder names kept, so an album lands as one item. Until now the only way
 * in was copying to the folder on disk, which for a docker install meant
 * finding the mount.
 */
export function UploadZone({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [over, setOver] = useState(false);
  const [rows, setRows] = useState<UploadRow[]>([]);
  const nextId = useRef(1);
  const depth = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  // the folder picker needs the non-standard attribute set by hand;
  // react does not know it
  useEffect(() => {
    folderInput.current?.setAttribute('webkitdirectory', '');
    folderInput.current?.setAttribute('directory', '');
  }, []);

  const upload = async (picked: Picked[]) => {
    const audio = picked.filter((p) => isAudio(p.file.name));
    const skipped = picked.length - audio.length;
    if (audio.length === 0) {
      window.showToast?.(skipped ? 'No audio files in that drop' : 'Nothing to upload', 'warning');
      return;
    }
    const batch: UploadRow[] = audio.map((p) => ({
      id: nextId.current++,
      path: p.path,
      size: p.file.size,
      fraction: 0,
      status: 'queued',
    }));
    setRows((current) => [...current, ...batch]);
    const patch = (id: number, next: Partial<UploadRow>) =>
      setRows((current) => current.map((row) => (row.id === id ? { ...row, ...next } : row)));

    // two at a time: enough to keep a link busy, few enough that one huge
    // file does not starve the rest
    let index = 0;
    let landed = 0;
    let failed = 0;
    const worker = async () => {
      while (index < audio.length) {
        const i = index++;
        const row = batch[i];
        patch(row.id, { status: 'uploading' });
        try {
          const result = await uploadImportFile(audio[i].file, audio[i].path, (fraction) =>
            patch(row.id, { fraction }),
          );
          const skippedHere = result.skipped?.[0];
          if (skippedHere) {
            failed += 1;
            patch(row.id, { status: 'skipped', note: skippedHere.reason, fraction: 1 });
          } else {
            landed += 1;
            patch(row.id, { status: 'done', fraction: 1 });
          }
        } catch (error) {
          failed += 1;
          patch(row.id, { status: 'error', note: getErrorMessage(error) });
        }
      }
    };
    await Promise.all([worker(), worker()]);
    void invalidateImportStagingQueries(queryClient);
    if (landed > 0) {
      window.showToast?.(
        `${landed} ${landed === 1 ? 'file' : 'files'} in the import folder${failed ? `, ${failed} not` : ''}`,
        failed ? 'warning' : 'success',
      );
    } else {
      window.showToast?.('Nothing uploaded', 'error');
    }
  };

  const onDrop = async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    depth.current = 0;
    setOver(false);
    const picked = await collectDropped(event.dataTransfer.items);
    void upload(picked.length ? picked : fromInput(event.dataTransfer.files));
  };

  const active = rows.filter((r) => r.status === 'queued' || r.status === 'uploading');
  const finished = rows.length - active.length;

  return (
    <div
      className={clsx(styles.dropzone, { [styles.dropzoneOver]: over })}
      onDragEnter={(event) => {
        if (!Array.from(event.dataTransfer.types).includes('Files')) return;
        depth.current += 1;
        setOver(true);
      }}
      onDragLeave={() => {
        depth.current = Math.max(0, depth.current - 1);
        if (depth.current === 0) setOver(false);
      }}
      onDragOver={(event) => {
        if (!Array.from(event.dataTransfer.types).includes('Files')) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
      }}
      onDrop={(event) => void onDrop(event)}
    >
      {over ? (
        <div className={styles.dropOverlay} aria-hidden="true">
          <div className={styles.dropOverlayText}>Drop to add to the import folder</div>
          <div className={styles.dropOverlayHint}>
            Folders keep their names, so an album stays together
          </div>
        </div>
      ) : null}

      <input
        ref={fileInput}
        type="file"
        multiple
        accept="audio/*,.flac,.mp3,.m4a,.ogg,.opus,.wav,.aac,.wma,.aiff,.ape"
        hidden
        onChange={(event) => {
          void upload(fromInput(event.target.files));
          event.target.value = '';
        }}
      />
      <input
        ref={folderInput}
        type="file"
        multiple
        hidden
        onChange={(event) => {
          void upload(fromInput(event.target.files));
          event.target.value = '';
        }}
      />

      <div className={styles.uploadBar}>
        <span className={styles.uploadHint}>Drop files or folders anywhere here, or</span>
        <Button
          variant="secondary"
          size="sm"
          id="import-upload-files"
          onClick={() => fileInput.current?.click()}
        >
          Add files
        </Button>
        <Button
          variant="secondary"
          size="sm"
          id="import-upload-folder"
          onClick={() => folderInput.current?.click()}
        >
          Add a folder
        </Button>
        {rows.length > 0 ? (
          <>
            <span className={styles.toolbarSpacer} />
            <span className={styles.uploadHint}>
              {active.length > 0
                ? `Uploading ${finished + 1} of ${rows.length}`
                : `${finished} uploaded`}
            </span>
            {active.length === 0 ? (
              <Button variant="ghost" size="sm" onClick={() => setRows([])}>
                Clear
              </Button>
            ) : null}
          </>
        ) : null}
      </div>

      {rows.length > 0 ? (
        <div className={styles.uploadList} aria-label="Uploads">
          {rows.slice(-8).map((row) => (
            <div key={row.id} className={styles.uploadRow} data-status={row.status}>
              <span className={styles.uploadName} title={row.path}>
                {row.path}
              </span>
              <span className={styles.uploadSize}>{formatImportBytes(row.size)}</span>
              <span className={styles.progress}>
                <span
                  className={clsx(styles.progressFill, {
                    [styles.progressFailed]: row.status === 'error',
                  })}
                  style={{ width: `${Math.round(row.fraction * 100)}%` }}
                />
              </span>
              <span className={styles.uploadStatus}>
                {row.status === 'done'
                  ? 'done'
                  : row.status === 'uploading'
                    ? `${Math.round(row.fraction * 100)}%`
                    : row.status === 'queued'
                      ? 'waiting'
                      : row.note || row.status}
              </span>
            </div>
          ))}
          {rows.length > 8 ? (
            <div className={styles.uploadHint}>and {rows.length - 8} more</div>
          ) : null}
        </div>
      ) : null}

      {children}
    </div>
  );
}
