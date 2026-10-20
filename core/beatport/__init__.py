"""beatport, read from the json every beatport page already ships.

each page is a next.js app with its data in ``__NEXT_DATA__``: tracks with
their real release, isrc, mix name, artists, bpm and key. the old scraper read
the rendered html instead and then visited every track page to get back what
it had thrown away. this package reads the json.
"""
