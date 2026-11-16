/**
 * Connections > the video side's API Configuration (SETTINGS_PORT.md, video
 * pass V3a): TMDB, TVDB, OMDb, fanart.tv, the three subtitle providers, Trakt,
 * MDBList and the two keyless cards. Was index.html and video-settings.js;
 * the keys are video-keys.ts. Each card portals into a wrapper that sits in
 * this group, or in the services modal while its tile is open, as the music
 * cards do (connections-services.tsx useServicePlace).
 */

import { type ReactNode, useState } from 'react';
import { createPortal } from 'react-dom';

import { useCommit } from './commit';
import { toggleAll } from './connections-extras';
import { ServiceHome, useServicePlace } from './connections-services';
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
  children,
}: {
  service: string;
  colour: string;
  title: string;
  logo?: ReactNode;
  required?: boolean;
  children: ReactNode;
}) {
  const { wrap, open } = useServicePlace(service, ORDER.indexOf(service), 'video');
  return createPortal(
    <div className="api-service-frame stg-service" data-video-service={service} data-react-panel="">
      <div
        className="stg-service-header"
        onClick={(event) => event.currentTarget.parentElement?.classList.toggle('expanded')}
      >
        <span className="stg-service-dot" style={{ color: colour }}></span>
        {logo}
        <h4 className="service-title">{title}</h4>
        {required ? <span className="stg-req-pill">Required</span> : null}
        <span className="stg-service-chevron">›</span>
      </div>
      {/* collapsed by default as an accordion; in the modal it is the content */}
      <div className="stg-service-body" style={open ? { display: 'block' } : undefined}>
        {children}
      </div>
    </div>,
    wrap,
  );
}

/** a key: kept as typed, saved when the field is left */
function Key({
  field,
  label = 'API Key:',
  placeholder,
}: {
  field: KeyField;
  label?: string;
  placeholder: string;
}) {
  const { keys } = useVideoKeys();
  const left = useCommit<HTMLInputElement>(() => void videoKeys.save());
  return (
    <div className="form-group">
      <label>{label}</label>
      <input
        type="password"
        id={`${field}-api-key`}
        placeholder={placeholder}
        ref={left}
        value={keys[field]}
        onChange={(event) => videoKeys.type(field, event.target.value)}
      />
    </div>
  );
}

function Toggle({ k, children }: { k: Switch; children: ReactNode }) {
  const { on } = useVideoKeys();
  return (
    <label className="vid-pref-row">
      <input
        type="checkbox"
        id={`video-${k}-enabled`}
        checked={on[k]}
        onChange={(event) => void videoKeys.flip(k, event.target.checked)}
      />{' '}
      <span>{children}</span>
    </label>
  );
}

function Test({ spec, children }: { spec: string; children: ReactNode }) {
  return (
    <button
      className="test-button"
      type="button"
      data-video-test-service={spec}
      onClick={() => void testVideoServices(spec)}
    >
      {children}
    </button>
  );
}

function Help({ children }: { children: ReactNode }) {
  return <div className="callback-help">{children}</div>;
}

function Link({ href, colour, children }: { href: string; colour: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" style={{ color: colour }}>
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
        logo={<img className="video-svc-logo" src="/static/img/brands/tmdb.svg" alt="" />}
      >
        <Key field="tmdb" placeholder="The Movie Database API Key" />
        <div className="callback-info">
          <Help>
            Get your free key from{' '}
            <Link href="https://www.themoviedb.org/settings/api" colour="#01b4e4">
              TMDB API Settings
            </Link>
          </Help>
          <Help>
            <strong>Required.</strong> Primary movie &amp; TV metadata source &mdash; nothing on the
            video side works without it.
          </Help>
        </div>
        <Test spec="tmdb">Test TMDB</Test>
      </Card>
      <Card
        service="tvdb"
        colour="#6cd391"
        title="TVDB"
        required
        logo={
          <img
            className="video-svc-logo video-svc-logo--tvdb"
            src="/static/img/brands/tvdb.svg"
            alt=""
          />
        }
      >
        <Key field="tvdb" placeholder="TheTVDB API Key" />
        <div className="callback-info">
          <Help>
            Get your key from{' '}
            <Link href="https://thetvdb.com/dashboard/account/apikey" colour="#6cd391">
              TheTVDB API
            </Link>
          </Help>
          <Help>
            <strong>Required.</strong> TV series metadata &mdash; episodes &amp; air dates the
            airing automations rely on.
          </Help>
        </div>
        <Test spec="tvdb">Test TVDB</Test>
      </Card>
      <Card service="omdb" colour="#f5c518" title="OMDb (Ratings)">
        <Key field="omdb" placeholder="OMDb API Key" />
        <div className="callback-info">
          <Help>
            Get a free key from{' '}
            <Link href="https://www.omdbapi.com/apikey.aspx" colour="#f5c518">
              OMDb API
            </Link>
          </Help>
          <Help>IMDb / Rotten Tomatoes / Metacritic ratings.</Help>
        </div>
        <Test spec="omdb">Test OMDb</Test>
      </Card>
      <Card service="fanart" colour="#e4509a" title="fanart.tv (Artwork)">
        <Key field="fanart" placeholder="fanart.tv API Key" />
        <div className="callback-info">
          <Help>
            Get a free key from{' '}
            <Link href="https://fanart.tv/get-an-api-key/" colour="#e4509a">
              fanart.tv
            </Link>
          </Help>
          <Help>
            Clear logos, clearart, banners, posters &amp; backgrounds (gap-fills missing art).
          </Help>
        </div>
        <Test spec="fanart">Test fanart.tv</Test>
      </Card>
      <Card service="opensubtitles" colour="#22a079" title="OpenSubtitles (Subtitles)">
        <Key field="opensubtitles" placeholder="OpenSubtitles API Key" />
        <div className="callback-info">
          <Help>
            Get a free key from{' '}
            <Link href="https://www.opensubtitles.com/en/consumers" colour="#22a079">
              OpenSubtitles
            </Link>{' '}
            (register a consumer)
          </Help>
          <Help>Subtitle downloads, plus which subtitle languages exist per title.</Help>
        </div>
        <Test spec="opensubtitles">Test OpenSubtitles</Test>
      </Card>
      <Card service="subdl" colour="#7c6cf0" title="SubDL (Subtitles)">
        <Key field="subdl" placeholder="SubDL API Key" />
        <div className="callback-info">
          <Help>
            Get a free key from{' '}
            <Link href="https://subdl.com/api-doc" colour="#22a079">
              SubDL
            </Link>{' '}
            (account panel)
          </Help>
        </div>
        <Test spec="subdl">Test SubDL</Test>
      </Card>
      <Card service="subsource" colour="#4da3ff" title="SubSource (Subtitles)">
        <Key field="subsource" placeholder="SubSource API Key" />
        <div className="callback-info">
          <Help>
            Get a free key from{' '}
            <Link href="https://subsource.net/profile" colour="#22a079">
              SubSource
            </Link>{' '}
            (profile page)
          </Help>
        </div>
        <Test spec="subsource">Test SubSource</Test>
      </Card>
      <Card service="trakt" colour="#ed1c24" title="Trakt (Audience Rating)">
        <Key field="trakt" label="API Key (Client ID):" placeholder="Trakt Client ID" />
        <div className="callback-info">
          <Help>
            Create a free API app at{' '}
            <Link href="https://trakt.tv/oauth/applications" colour="#ed1c24">
              trakt.tv
            </Link>{' '}
            and paste its <strong>Client ID</strong>.
          </Help>
          <Help>
            Adds Trakt&apos;s community audience score (and vote count) to movies &amp; shows — and
            lets the Collection Studio build collections from public Trakt lists.
          </Help>
        </div>
        <Test spec="trakt">Test Trakt</Test>
      </Card>
      <Card service="mdblist" colour="#f5c518" title="MDBList (List Collections)">
        <Key field="mdblist" placeholder="MDBList API key" />
        <div className="callback-info">
          <Help>
            Free key from{' '}
            <Link href="https://mdblist.com/preferences/" colour="#f5c518">
              mdblist.com
            </Link>{' '}
            (Preferences &rarr; API Access).
          </Help>
          <Help>
            Lets the Collection Studio build collections from MDBList lists — the real IMDb Top 250,
            award lists (Oscar winners), and thousands of community lists.
          </Help>
        </div>
        <Test spec="mdblist">Test MDBList</Test>
      </Card>
      <Card service="nokey" colour="#3dd6c0" title="Community Data (No Key)">
        <Toggle k="tvmaze">TVmaze (TV community rating)</Toggle>
        <Toggle k="anilist">
          AniList (anime): links your anime to AniList by id and adds their romaji and English names
          to release searches, plus score, studio and the next episode. Only ever touches anime
        </Toggle>
        <Toggle k="wikidata">Wikidata (official website link)</Toggle>
        <div className="callback-info">
          <Help>
            Free community APIs — no key needed. Gap-fill extra data on your library in the
            background.
          </Help>
        </div>
        <Test spec="tvmaze,anilist,wikidata">Test Connections</Test>
      </Card>
      <Card service="ytextras" colour="#ef4444" title="YouTube Extras (No Key)">
        <Toggle k="ryd">Return YouTube Dislike (like/dislike estimates)</Toggle>
        <Toggle k="sponsorblock">SponsorBlock (crowd-sourced segments)</Toggle>
        <Toggle k="dearrow">DeArrow (crowd-sourced better titles)</Toggle>
        <div className="callback-info">
          <Help>
            Free community APIs — no key needed. Enrich cached YouTube videos in the background.
          </Help>
        </div>
        <Test spec="ryd,sponsorblock,dearrow">Test Connections</Test>
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
      <h3>
        API Configuration{' '}
        <button
          className="stg-accordion-toggle"
          onClick={(event) => toggleAll(event.currentTarget)}
        >
          Expand All
        </button>
      </h3>
      <ServiceHome.Provider value={home}>
        <Cards />
      </ServiceHome.Provider>
    </div>
  );
}
