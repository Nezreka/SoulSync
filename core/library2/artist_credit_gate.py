"""Keep unresolved combined credits out of every enrichment worker queue."""

CATALOG_PROVIDERS = ('spotify', 'musicbrainz', 'deezer', 'itunes', 'tidal',
                     'qobuz', 'amazon', 'jiosaavn', 'bandcamp')


def unresolved_credit_sql(alias):
    """Conservative candidate gate; the existing resolver decides band vs credit.

    Punctuation alone never splits an artist. A confirmed provider identity
    admits bands such as Earth, Wind & Fire. An unconfirmed combined name stays
    with the identity reconciler instead of all sixteen enrichment workers.
    """
    if not alias.isidentifier():
        raise ValueError('Invalid artist alias')
    name = f'{alias}.name'
    compound = ' OR '.join([f"{name} GLOB '*[,;&/+×]*'", *(
        f"{name} LIKE '% {marker} %'" for marker in
        ('feat', 'feat.', 'ft', 'ft.', 'featuring', 'with', 'and', 'x', 'vs', 'vs.')),
        *(f"{name} LIKE '%{marker} %'" for marker in ('(feat', '(feat.', '(ft', '(ft.',
                                                               '(featuring', '[feat', '[feat.',
                                                               '[ft', '[ft.', '[featuring'))])
    ids = f"CASE WHEN json_valid({alias}.external_ids) THEN {alias}.external_ids ELSE '{{}}' END"
    identity = ' OR '.join([
        f"COALESCE({alias}.spotify_id,'')<>''",
        f"COALESCE({alias}.musicbrainz_id,'')<>''",
        *(f"COALESCE(json_extract({ids},'$.{provider}'),'')<>''" for provider in CATALOG_PROVIDERS),
    ])
    return f'(({compound}) AND NOT ({identity}))'


def worker_credit_ready_sql(entity, alias='e'):
    if entity == 'artist':
        return f'NOT {unresolved_credit_sql(alias)}'
    if entity == 'album':
        parent = f'a.id={alias}.primary_artist_id'
        tables = 'lib2_artists a'
    elif entity == 'track':
        parent = f'al.id={alias}.album_id'
        tables = 'lib2_albums al JOIN lib2_artists a ON a.id=al.primary_artist_id'
    else:
        raise ValueError('Unknown entity')
    return f'NOT EXISTS (SELECT 1 FROM {tables} WHERE {parent} AND {unresolved_credit_sql("a")})'
