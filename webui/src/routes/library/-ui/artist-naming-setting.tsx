/**
 * #2165 "Folder and tag name": what new downloads of an artist are filed and
 * tagged as, on the artist's own settings sheet.
 *
 * A download is named after the library artist it belongs to, found by id, so
 * a source that still says "Machine Gun Kelly" files under "mgk" with nothing
 * to set up. A name typed here is the artist's name override (the same one
 * Edit Metadata writes): it wins for folders, tags and the name SoulSync shows.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { type FormEvent, useEffect, useId, useState } from 'react';

import {
  LIBRARY_V2_QUERY_KEY,
  type LibraryV2ArtistNaming,
  fetchLibraryV2ArtistNaming,
  updateLibraryV2MetadataOverrides,
} from '../-library-v2.api';
import { SettingsGroup, ls } from './library-settings-parts';

/** The one line that says what happens to a new download, null when the
 *  source and the library agree. */
export function namingSentence(naming: LibraryV2ArtistNaming): string | null {
  const { source, library_name: name } = naming;
  if (source && naming.renamed) {
    return `${source.label} calls them ${source.name}. SoulSync files them as ${name}, ${
      naming.pinned ? 'the name you set' : 'the name in your library'
    }.`;
  }
  if (source && naming.kept_source_name) {
    return `${source.label} calls them ${source.name}, and so does another artist in your library. Set a name here to choose.`;
  }
  if (naming.pinned) return `New downloads are filed and tagged as ${name}, the name you set.`;
  return null;
}

function errorText(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : 'The name could not be saved';
}

function FolderGlyph() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
      <path
        d="M1.75 4.25c0-.83.67-1.5 1.5-1.5h3l1.5 1.75h5c.83 0 1.5.67 1.5 1.5v5.75c0 .83-.67 1.5-1.5 1.5h-9.5c-.83 0-1.5-.67-1.5-1.5z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function ArtistNamingSetting({
  artistId,
  canWrite,
  onPreviewRename,
  onPreviewRetag,
}: {
  artistId: number;
  canWrite: boolean;
  /** opens Preview rename for the artist's files already in the library */
  onPreviewRename?: () => void;
  /** opens Preview retag for them */
  onPreviewRetag?: () => void;
}) {
  const queryClient = useQueryClient();
  const inputId = useId();
  const query = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'artist-naming', artistId],
    queryFn: () => fetchLibraryV2ArtistNaming(artistId),
  });
  const naming = query.data;
  const [draft, setDraft] = useState('');
  useEffect(() => {
    if (naming) setDraft(naming.library_name);
  }, [naming]);

  const save = useMutation({
    mutationFn: (name: string | null) =>
      name === null
        ? updateLibraryV2MetadataOverrides('artist', artistId, {}, ['name'])
        : updateLibraryV2MetadataOverrides('artist', artistId, { name }),
    // the name shows across the page, so everything Library refreshes
    onSuccess: () => queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY }),
  });

  if (query.isError) {
    return (
      <SettingsGroup title="Folder and tag name">
        <div className={ls.empty} role="alert">
          {query.error instanceof Error && query.error.message.trim()
            ? query.error.message
            : 'The artist name could not be loaded'}
        </div>
      </SettingsGroup>
    );
  }
  if (!naming) {
    return (
      <SettingsGroup title="Folder and tag name">
        <div className={ls.nameCard} aria-busy="true">
          <span className={ls.nameLabel}>Loading…</span>
        </div>
      </SettingsGroup>
    );
  }

  const typed = draft.trim();
  // typing the scanned name back on a pinned artist is "go back to it"
  const backToScanned = naming.pinned && typed === naming.scanned_name;
  const changed = typed !== '' && typed !== naming.library_name;
  const sentence = namingSentence(naming);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!canWrite || !changed || save.isPending) return;
    save.mutate(backToScanned ? null : typed);
  }

  return (
    <SettingsGroup
      title="Folder and tag name"
      hint="What new downloads of this artist are filed and tagged as."
    >
      <div className={`${ls.nameCard} ${naming.renamed ? ls.nameCardRenamed : ''}`}>
        {/* the source's name only when it differs: two equal names say nothing */}
        {naming.source && (naming.renamed || naming.kept_source_name) ? (
          <div className={ls.nameLine}>
            <span className={ls.nameLabel}>{naming.source.label} calls them</span>
            <span className={`${ls.nameValue} ${naming.renamed ? ls.nameFrom : ''}`}>
              {naming.source.name}
            </span>
          </div>
        ) : null}
        <div className={ls.nameLine}>
          <span className={ls.nameLabel}>Filed and tagged as</span>
          <span className={ls.nameValue}>{naming.library_name}</span>
          <span className={`${ls.nameTag} ${naming.pinned ? ls.nameTagSet : ''}`}>
            {naming.pinned ? 'Set by you' : 'From your library'}
          </span>
        </div>
        {naming.folder ? (
          <code className={ls.nameFolder} title="Where a new album goes">
            <FolderGlyph />
            <span>{naming.folder}/</span>
          </code>
        ) : null}
      </div>
      {sentence ? <p className={ls.nameSay}>{sentence}</p> : null}

      {/* #2210: the name is the admin's to change; a member reads it above */}
      {canWrite ? (
        <form className={ls.nameForm} onSubmit={submit}>
          <label className={ls.nameFieldLabel} htmlFor={inputId}>
            Name for folders and tags
          </label>
          <div className={ls.nameRow}>
            <input
              id={inputId}
              className={ls.nameInput}
              value={draft}
              maxLength={200}
              spellCheck={false}
              autoComplete="off"
              disabled={!canWrite || save.isPending}
              onChange={(event) => setDraft(event.target.value)}
            />
            <button
              type="submit"
              className={`${ls.capsule} ${ls.capsuleAccent}`}
              disabled={!canWrite || !changed || save.isPending}
            >
              {save.isPending ? 'Saving…' : 'Save name'}
            </button>
          </div>
          <small className={ls.nameNote}>
            Also the name SoulSync shows.
            {naming.pinned && naming.scanned_name && naming.scanned_name !== naming.library_name ? (
              <>
                {' '}
                <button
                  type="button"
                  className={ls.nameLink}
                  disabled={!canWrite || save.isPending}
                  onClick={() => save.mutate(null)}
                >
                  Go back to {naming.scanned_name}
                </button>
              </>
            ) : null}
          </small>
          {save.isError ? (
            <div className={ls.nameError} role="alert">
              {errorText(save.error)}
            </div>
          ) : null}
        </form>
      ) : null}

      {canWrite && (onPreviewRename || onPreviewRetag) ? (
        <div className={ls.nameExisting}>
          <span>Files you already have stay put until you move them.</span>
          <div className={ls.chips}>
            {onPreviewRename ? (
              <button type="button" className={ls.capsule} onClick={onPreviewRename}>
                Preview rename
              </button>
            ) : null}
            {onPreviewRetag ? (
              <button type="button" className={ls.capsule} onClick={onPreviewRetag}>
                Preview retag
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </SettingsGroup>
  );
}
