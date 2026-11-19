/**
 * Connections > the video side's services (SETTINGS_PORT.md, video pass V3a,
 * rebuilt on the settings parts in settings-bic phase 3): TMDB, TVDB, OMDb,
 * fanart.tv, the three subtitle providers, Trakt, MDBList and the two keyless
 * cards. The keys are video-keys.ts. Each card portals into a wrapper that
 * sits in this group, or in the service sheet while its tile is open, as the
 * music cards do (connections-services.tsx ServiceFrame); the sheet's Test
 * calls the card's own test.
 */

import { type ReactNode, useState } from 'react';

import { Note, Row, SheetField, Switch as SwitchCtl, TextField } from '../ui';
import { useCommit } from './commit';
import { ServiceFrame, ServiceHome } from './connections-services';
import {
  type KeyField,
  type Switch,
  testVideoServices,
  useVideoKeys,
  videoKeys,
} from './video-keys';

/** the cards in the group's order (the wrappers keep it) */
const ORDER = [
  'tmdb',
  'tvdb',
  'omdb',
  'fanart',
  'opensubtitles',
  'subdl',
  'subsource',
  'trakt',
  'mdblist',
  'nokey',
  'ytextras',
];

function Card({
  service,
  colour,
  title,
  logo,
  required,
  test,
  children,
}: {
  service: string;
  colour: string;
  title: string;
  logo?: string;
  required?: boolean;
  /** what the sheet's Test tests (testVideoServices) */
  test: string;
  children: ReactNode;
}) {
  return (
    <ServiceFrame
      kind="video"
      service={service}
      order={ORDER.indexOf(service)}
      colour={colour}
      title={title}
      logo={logo}
      required={required}
      extra={{ 'data-video-test-service': test }}
      test={() => testVideoServices(test)}
    >
      {children}
    </ServiceFrame>
  );
}

/** a key: kept as typed, saved when the field is left */
function Key({
  field,
  label = 'API key',
  placeholder,
  help,
  more,
}: {
  field: KeyField;
  label?: string;
  placeholder: string;
  help: ReactNode;
  more?: ReactNode;
}) {
  const { keys } = useVideoKeys();
  const left = useCommit<HTMLInputElement>(() => void videoKeys.save());
  return (
    <SheetField label={label} htmlFor={`${field}-api-key`} help={help} more={more}>
      <TextField
        full
        type="password"
        id={`${field}-api-key`}
        placeholder={placeholder}
        autoComplete="off"
        ref={left}
        value={keys[field]}
        onChange={(event) => videoKeys.type(field, event.target.value)}
      />
    </SheetField>
  );
}

function Toggle({ k, label, help }: { k: Switch; label: string; help?: string }) {
  const { on } = useVideoKeys();
  return (
    <Row label={label} htmlFor={`video-${k}-enabled`} help={help}>
      <SwitchCtl
        id={`video-${k}-enabled`}
        checked={on[k]}
        onChange={(event) => void videoKeys.flip(k, event.target.checked)}
      />
    </Row>
  );
}

function Link({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

function Cards() {
  return (
    <>
      <Card
        service="tmdb"
        colour="#01b4e4"
        title="TMDB"
        required
        logo="/static/img/brands/tmdb.svg"
        test="tmdb"
      >
        <Note>
          <strong>Required.</strong> The main movie and TV metadata source: nothing on the video
          side works without it.
        </Note>
        <Key
          field="tmdb"
          placeholder="The Movie Database API Key"
          help={
            <>
              A free key from{' '}
              <Link href="https://www.themoviedb.org/settings/api">TMDB API Settings</Link>.
            </>
          }
        />
      </Card>
      <Card
        service="tvdb"
        colour="#6cd391"
        title="TVDB"
        required
        logo="/static/img/brands/tvdb.svg"
        test="tvdb"
      >
        <Note>
          <strong>Required.</strong> TV series metadata: the episodes and air dates the airing
          automations rely on.
        </Note>
        <Key
          field="tvdb"
          placeholder="TheTVDB API Key"
          help={
            <>
              Get a key from{' '}
              <Link href="https://thetvdb.com/dashboard/account/apikey">TheTVDB API</Link>.
            </>
          }
        />
      </Card>
      <Card service="omdb" colour="#f5c518" title="OMDb (Ratings)" test="omdb">
        <Key
          field="omdb"
          placeholder="OMDb API Key"
          help={
            <>
              IMDb, Rotten Tomatoes and Metacritic ratings. A free key from{' '}
              <Link href="https://www.omdbapi.com/apikey.aspx">OMDb API</Link>.
            </>
          }
        />
      </Card>
      <Card service="fanart" colour="#e4509a" title="fanart.tv (Artwork)" test="fanart">
        <Key
          field="fanart"
          placeholder="fanart.tv API Key"
          help={
            <>
              Clear logos, clearart, banners, posters and backgrounds where art is missing. A free
              key from <Link href="https://fanart.tv/get-an-api-key/">fanart.tv</Link>.
            </>
          }
        />
      </Card>
      <Card
        service="opensubtitles"
        colour="#22a079"
        title="OpenSubtitles (Subtitles)"
        test="opensubtitles"
      >
        <Key
          field="opensubtitles"
          placeholder="OpenSubtitles API Key"
          help={
            <>
              Subtitle downloads, and which subtitle languages exist for each title. A free key from{' '}
              <Link href="https://www.opensubtitles.com/en/consumers">OpenSubtitles</Link> (register
              a consumer).
            </>
          }
        />
      </Card>
      <Card service="subdl" colour="#7c6cf0" title="SubDL (Subtitles)" test="subdl">
        <Key
          field="subdl"
          placeholder="SubDL API Key"
          help={
            <>
              A free key from <Link href="https://subdl.com/api-doc">SubDL</Link> (account panel).
            </>
          }
        />
      </Card>
      <Card service="subsource" colour="#4da3ff" title="SubSource (Subtitles)" test="subsource">
        <Key
          field="subsource"
          placeholder="SubSource API Key"
          help={
            <>
              A free key from <Link href="https://subsource.net/profile">SubSource</Link> (profile
              page).
            </>
          }
        />
      </Card>
      <Card service="trakt" colour="#ed1c24" title="Trakt (Audience Rating)" test="trakt">
        <Key
          field="trakt"
          label="API key (Client ID)"
          placeholder="Trakt Client ID"
          help={
            <>
              Create a free API app at{' '}
              <Link href="https://trakt.tv/oauth/applications">trakt.tv</Link> and paste its Client
              ID.
            </>
          }
          more="Adds Trakt's community audience score (and vote count) to movies and shows, and lets the Collection Studio build collections from public Trakt lists."
        />
      </Card>
      <Card service="mdblist" colour="#f5c518" title="MDBList (List Collections)" test="mdblist">
        <Key
          field="mdblist"
          placeholder="MDBList API key"
          help={
            <>
              A free key from <Link href="https://mdblist.com/preferences/">mdblist.com</Link>{' '}
              (Preferences, API Access).
            </>
          }
          more="Lets the Collection Studio build collections from MDBList lists: the real IMDb Top 250, award lists (Oscar winners), and thousands of community lists."
        />
      </Card>
      <Card
        service="nokey"
        colour="#3dd6c0"
        title="Community Data (No Key)"
        test="tvmaze,anilist,wikidata"
      >
        <Note info>
          Free community services, no key needed. They fill in extra data on your library in the
          background.
        </Note>
        <Toggle k="tvmaze" label="TVmaze" help="The TV community rating." />
        <Toggle
          k="anilist"
          label="AniList"
          help="Anime only: links your anime to AniList, adds their romaji and English names to release searches, plus score, studio and the next episode."
        />
        <Toggle k="wikidata" label="Wikidata" help="The official website link." />
      </Card>
      <Card
        service="ytextras"
        colour="#ef4444"
        title="YouTube Extras (No Key)"
        test="ryd,sponsorblock,dearrow"
      >
        <Note info>
          Free community services, no key needed. They enrich cached YouTube videos in the
          background.
        </Note>
        <Toggle k="ryd" label="Return YouTube Dislike" help="Like and dislike estimates." />
        <Toggle k="sponsorblock" label="SponsorBlock" help="Crowd-sourced segments." />
        <Toggle k="dearrow" label="DeArrow" help="Crowd-sourced better titles." />
      </Card>
    </>
  );
}

/** the group the cards live in while closed (hidden: the tiles open them) */
export function VideoApiGroup() {
  const [home, setHome] = useState<HTMLElement | null>(null);
  return (
    <div
      className="settings-group"
      data-stg="connections"
      data-video-only=""
      data-svc-legacy=""
      ref={setHome}
    >
      <ServiceHome.Provider value={home}>
        <Cards />
      </ServiceHome.Provider>
    </div>
  );
}
