"""The AcoustID decision core against REAL fingerprint answers.

139 files that actually sit in a working SoulSync library, each with the
recordings AcoustID really returned for its fingerprint (captured live via
fpcalc + api.acoustid.org): Hiroyuki Sawano's Attack on Titan scores,
PianoPrinceOfAnime's epic-cover catalogue, Michael Jackson, Justin Bieber,
James Brown.

WHAT THIS FILE ACTUALLY DEFENDS — established by mutation, not by assertion.
Breaking each of these makes it go red, and nothing else in the suite catches
them:

  * the cross-script artist bridge (46 rows). Tags say 'Hiroyuki Sawano',
    MusicBrainz credits 澤野弘之, direct artist similarity is 0.00, and only the
    alias provider rescues it. Drop the provider and 46 correct files are
    quarantined.
  * CJK identity in normalize() (70 rows). Narrow the character class from
    ``\\w`` to ASCII and every Japanese title becomes an empty string.
  * the FAIL/SKIP threshold (6 rows).

It is NOT a test of the version-tail rule — only 6 of these titles carry a dash
tail at all, and mutating that rule leaves this file green. The tail rule is
covered by ``_TAIL_PAIRS`` at the bottom of this file and by
``tests/text/test_version_qualifier_corpus.py``.
"""

import pytest

from core.matching.audio_verification import Decision, evaluate, normalize, similarity


_ALIASES = {
    'hiroyuki sawano': ['澤野弘之', 'Sawano Hiroyuki', 'SawanoHiroyuki[nZk]'],
    'michael jackson': ['マイケル・ジャクソン'],
}


def _aliases_for(artist):
    return lambda: _ALIASES.get((artist or '').strip().lower(), [])


# (expected_title, expected_artist, [(acoustid_title, acoustid_artist), ...],
#  fingerprint_score, decision)
_REAL_LOOKUPS = [
    ('2Volt', 'Hiroyuki Sawano',
     [('2Volt', '澤野弘之')], 0.99, 'pass'),
    ('2chijou', 'Hiroyuki Sawano',
     [('2chi城', '澤野弘之')], 0.99, 'skip'),
    ('APETITAN', 'Hiroyuki Sawano',
     [('APETITAN', '澤野弘之')], 0.99, 'pass'),
    ('Aots2m #1', 'Hiroyuki Sawano',
     [('AOTs2M他1', '澤野弘之')], 0.99, 'pass'),
    ('Aots2m #2', 'Hiroyuki Sawano',
     [('AOTs2M他2', '澤野弘之')], 1.0, 'pass'),
    ('Aots2m #3', 'Hiroyuki Sawano',
     [('AOTs2M他3', '澤野弘之')], 1.0, 'pass'),
    ('Aots2m #4', 'Hiroyuki Sawano',
     [('AOTs2M他4', '澤野弘之')], 1.0, 'pass'),
    ('Army-Attack', 'Hiroyuki Sawano',
     [('army⇒G♂', '澤野弘之')], 0.98, 'skip'),
    ('Attack on D', 'Hiroyuki Sawano',
     [('attack音D', '澤野弘之')], 1.0, 'pass'),
    ('Attack on Titan', 'Hiroyuki Sawano',
     [('ətˈæk 0N tάɪtn', '澤野弘之 <Vocal: MIKA KOBAYASHI>'), ('(intro) Attack on Titan', '小林未郁')], 0.99, 'skip'),
    ('Barricades', 'Hiroyuki Sawano',
     [('Barricades', '澤野弘之')], 0.99, 'pass'),
    ('Bauklotze', 'Hiroyuki Sawano',
     [('Bauklötze', '澤野弘之 <Vocal: MIKA KOBAYASHI>')], 0.98, 'pass'),
    ('Call Your Name', 'Hiroyuki Sawano',
     [('Call your name', '澤野弘之 <Vocal: mpi & CASG (Caramel Apple Sound Gadget)>')], 0.99, 'pass'),
    ('Call of Silence', 'Hiroyuki Sawano',
     [('Call of Silence', '澤野弘之')], 1.0, 'pass'),
    ('Counter Attack-Mankind', 'Hiroyuki Sawano',
     [('cóunter・attàck-mˈænkάɪnd', '澤野弘之')], 0.98, 'pass'),
    ('DOA', 'Hiroyuki Sawano',
     [('DOA', '澤野弘之 <Vocal: AIMEE BLACKSCHLEGER>')], 0.99, 'pass'),
    ('E.M.A', 'Hiroyuki Sawano',
     [('E・M・A', '澤野弘之')], 0.97, 'pass'),
    ('EMAymniam', 'Hiroyuki Sawano',
     [('EMAymniam', '澤野弘之')], 0.96, 'pass'),
    ('Eye-Water', 'Hiroyuki Sawano',
     [('eye-water', '澤野弘之')], 0.98, 'pass'),
    ('Omake-Pfadlib', 'Hiroyuki Sawano',
     [('omake-pfadlib', '澤野弘之')], 0.97, 'pass'),
    ('Rittaikidou', 'Hiroyuki Sawano',
     [('立body機motion', '澤野弘之')], 0.98, 'skip'),
    ('Shingeki Gt 20130218 Kyojin', 'Hiroyuki Sawano',
     [('進撃gt20130218巨人', '澤野弘之'), ('eye-water', '澤野弘之')], 0.94, 'skip'),
    ('Shingeki Pf - Adlib - B 20130218 Kyojin', 'Hiroyuki Sawano',
     [('Vogel im Käfig', '澤野弘之 <Vocal: Cyua>'), ('進撃pf-adlib-b20130218巨人', '澤野弘之')], 0.97, 'skip'),
    ('Shingeki Pf - Adlib - C 20130218 Kyojin', 'Hiroyuki Sawano',
     [('進撃pf-adlib-c20130218巨人', '澤野弘之'), ('凸】♀】♂】←巨人', '澤野弘之')], 0.96, 'skip'),
    ('Shingeki Pf - Medley 20130629 Kyojin', 'Hiroyuki Sawano',
     [('巨♀〜9地区', '澤野弘之'), ('進撃pf-medley20130629巨人', '澤野弘之')], 0.99, 'skip'),
    ('Shingeki Pf 20130218 Kyojin', 'Hiroyuki Sawano',
     [('The Reluctant Heroes', '澤野弘之 <Vocal: mpi>'), ('進撃pf20130218巨人', '澤野弘之')], 0.95, 'skip'),
    ('Shingeki St - Hrn - Gt 20130629 Kyojin', 'Hiroyuki Sawano',
     [('ətˈæk 0N tάɪtn', '澤野弘之 <Vocal: MIKA KOBAYASHI>'), ('進撃st-hrn-gt20130629巨人', '澤野弘之')], 0.99, 'skip'),
    ('Shingeki St - Hrn- Egt 20130629 Kyojin', 'Hiroyuki Sawano',
     [('ətˈæk 0N tάɪtn', '澤野弘之 <Vocal: MIKA KOBAYASHI>'), ('進撃st-hrn-egt20130629巨人', '澤野弘之')], 0.97, 'skip'),
    ('Shingeki St 20130629 Kyojin', 'Hiroyuki Sawano',
     [('進撃st20130629巨人', '澤野弘之'), ('巨♀〜9地区', '澤野弘之')], 0.99, 'skip'),
    ('Shingeki Vn - Pf 20130524 Kyojin', 'Hiroyuki Sawano',
     [('進撃vn-pf20130524巨人', '澤野弘之'), ('army⇒G♂', '澤野弘之')], 0.98, 'skip'),
    ('So ist es immer', 'Hiroyuki Sawano',
     [('So ist es immer', '澤野弘之')], 1.0, 'pass'),
    ('TWO-lives', 'Hiroyuki Sawano',
     [('TWO-lives', '澤野弘之')], 0.97, 'pass'),
    ('The Reluctant Heroes', 'Hiroyuki Sawano',
     [('The Reluctant Heroes', '澤野弘之 <Vocal: mpi>')], 0.99, 'pass'),
    ('The Reluctant Heroes <MODv>', 'Hiroyuki Sawano',
     [('The Reluctant Heroes', '澤野弘之 <Vocal: mpi>')], 0.99, 'pass'),
    ('TheWeightOfLives', 'Hiroyuki Sawano',
     [('TheWeightOfLives', '澤野弘之')], 0.97, 'pass'),
    ('Titan♀～9chiku', 'Hiroyuki Sawano',
     [('巨♀〜9地区', '澤野弘之')], 0.98, 'skip'),
    ('Vogel Im Kafig', 'Hiroyuki Sawano',
     [('Vogel im Käfig', '澤野弘之 <Vocal: Cyua>')], 1.0, 'pass'),
    ('Xl-Tt', 'Hiroyuki Sawano',
     [('XL-TT', '澤野弘之')], 0.95, 'pass'),
    ('YAMANAIAME', 'Hiroyuki Sawano',
     [('YAMANAIAME', '澤野弘之'), ('YAMANAIAME (Instrumental)', '澤野弘之')], 0.99, 'pass'),
    ('YAMANAIAME <FMv>', 'Hiroyuki Sawano',
     [('YAMANAIAME', '澤野弘之'), ('YAMANAIAME (Instrumental)', '澤野弘之')], 0.99, 'pass'),
    ('YouSeeBIGGIRL/T:T', 'Hiroyuki Sawano',
     [('YouSeeBIGGIRL/T:T', '澤野弘之')], 0.97, 'pass'),
    ('son2seaVer', 'Hiroyuki Sawano',
     [('son2seaVer', '澤野弘之')], 0.99, 'pass'),
    ('theDOGS', 'Hiroyuki Sawano',
     [('theDOGS', '澤野弘之'), ('theDOGS (Instrumental)', '澤野弘之')], 0.99, 'pass'),
    ('ymniam-MKorch', 'Hiroyuki Sawano',
     [('ymniam-MKorch', '澤野弘之')], 0.98, 'pass'),
    ('ymniam-orch', 'Hiroyuki Sawano',
     [('ymniam-orch (sm2_Final#2)', '澤野弘之')], 0.97, 'pass'),
    ('凸】♀】♂】←Titan', 'Hiroyuki Sawano',
     [('凸】♀】♂】←巨人', '澤野弘之')], 0.96, 'skip'),
    ('405', 'Justin Bieber',
     [('405', 'Justin Bieber')], 0.99, 'pass'),
    ('405', 'Justin Bieber',
     [('405', 'Justin Bieber')], 0.97, 'pass'),
    ('ALL I CAN TAKE', 'Justin Bieber',
     [('ALL I CAN TAKE', 'Justin Bieber')], 1.0, 'pass'),
    ('ALL I CAN TAKE', 'Justin Bieber',
     [('ALL I CAN TAKE', 'Justin Bieber')], 0.98, 'pass'),
    ('ALL THE WAY', 'Justin Bieber',
     [('ALL THE WAY', 'Justin Bieber')], 0.97, 'pass'),
    ('BAD HONEY', 'Justin Bieber',
     [('BAD HONEY', 'Justin Bieber')], 0.94, 'pass'),
    ('BETTER MAN', 'Justin Bieber',
     [('BETTER MAN', 'Justin Bieber')], 0.92, 'pass'),
    ('BUTTERFLIES', 'Justin Bieber',
     [('BUTTERFLIES', 'Justin Bieber')], 0.99, 'pass'),
    ('BUTTERFLIES', 'Justin Bieber',
     [('BUTTERFLIES', 'Justin Bieber')], 0.99, 'pass'),
    ('DADZ LOVE', 'Justin Bieber',
     [('DADZ LOVE', 'Justin Bieber & Lil B')], 0.97, 'pass'),
    ('DADZ LOVE', 'Justin Bieber',
     [('DADZ LOVE', 'Justin Bieber & Lil B')], 0.97, 'pass'),
    ('DAISIES', 'Justin Bieber',
     [('DAISIES', 'Justin Bieber'), ('DAISIES', 'Justin Bieber'), ('Daisies', 'Justin Bieber')], 0.97, 'pass'),
    ('DAISIES', 'Justin Bieber',
     [('DAISIES', 'Justin Bieber'), ('DAISIES', 'Justin Bieber'), ('Daisies', 'Justin Bieber')], 0.98, 'pass'),
    ('DEVOTION', 'Justin Bieber',
     [('DEVOTION', 'Justin Bieber & Dijon')], 0.99, 'pass'),
    ('DEVOTION', 'Justin Bieber',
     [('DEVOTION', 'Justin Bieber & Dijon')], 0.99, 'pass'),
    ("DON'T WANNA", 'Justin Bieber',
     [('DON’T WANNA', 'Justin Bieber & Bakar')], 0.96, 'pass'),
    ('DOTTED LINE', 'Justin Bieber',
     [('DOTTED LINE', 'Justin Bieber')], 0.97, 'pass'),
    ('EVERYTHING HALLELUJAH', 'Justin Bieber',
     [('EVERYTHING HALLELUJAH', 'Justin Bieber')], 0.97, 'pass'),
    ('EYE CANDY', 'Justin Bieber',
     [('EYE CANDY', 'Justin Bieber')], 0.98, 'pass'),
    ('FIRST PLACE', 'Justin Bieber',
     [('FIRST PLACE', 'Justin Bieber')], 1.0, 'pass'),
    ('FIRST PLACE', 'Justin Bieber',
     [('FIRST PLACE', 'Justin Bieber')], 0.98, 'pass'),
    ('FORGIVENESS', 'Justin Bieber',
     [('FORGIVENESS', 'Marvin Winans')], 0.99, 'skip'),
    ('FORGIVENESS', 'Justin Bieber',
     [('FORGIVENESS', 'Marvin Winans')], 0.98, 'skip'),
    ('GLORY VOICE MEMO', 'Justin Bieber',
     [('GLORY VOICE MEMO', 'Justin Bieber')], 0.97, 'pass'),
    ('GLORY VOICE MEMO', 'Justin Bieber',
     [('GLORY VOICE MEMO', 'Justin Bieber')], 0.99, 'pass'),
    ('GO BABY', 'Justin Bieber',
     [('GO BABY', 'Justin Bieber')], 0.96, 'pass'),
    ('I DO', 'Justin Bieber',
     [('I DO', 'Justin Bieber')], 0.97, 'pass'),
    ("I THINK YOU'RE SPECIAL", 'Justin Bieber',
     [("I THINK YOU'RE SPECIAL", 'Justin Bieber & Tems')], 0.99, 'pass'),
    ('LOVE SONG', 'Justin Bieber',
     [('LOVE SONG', 'Justin Bieber')], 0.97, 'pass'),
    ("LYIN'", 'Justin Bieber',
     [('LYIN’', 'Justin Bieber')], 0.99, 'pass'),
    ('MOTHER IN YOU', 'Justin Bieber',
     [('MOTHER IN YOU', 'Justin Bieber')], 0.97, 'pass'),
    ('MOVING FAST', 'Justin Bieber',
     [('MOVING FAST', 'Justin Bieber')], 0.97, 'pass'),
    ('NEED IT', 'Justin Bieber',
     [('NEED IT', 'Justin Bieber')], 0.97, 'pass'),
    ('OH MAN', 'Justin Bieber',
     [('OH MAN', 'Justin Bieber')], 0.97, 'pass'),
    ('OPEN UP YOUR HEART', 'Justin Bieber',
     [('OPEN UP YOUR HEART', 'Justin Bieber & Eddie Benjamin')], 0.97, 'pass'),
    ('PETTING ZOO', 'Justin Bieber',
     [('PETTING ZOO', 'Justin Bieber')], 0.98, 'pass'),
    ('POPPIN’ MY S***', 'Justin Bieber',
     [('POPPIN’ MY S***', 'Justin Bieber & Hurricane Chris')], 0.98, 'pass'),
    ('SAFE SPACE', 'Justin Bieber',
     [('SAFE SPACE', 'Justin Bieber & Lil B')], 0.97, 'pass'),
    ('SOULFUL', 'Justin Bieber',
     [('SOULFUL', 'Justin Bieber & Druski'), ('SOULFUL', 'Justin Bieber & Druski')], 0.99, 'pass'),
    ('SOULFUL', 'Justin Bieber',
     [('SOULFUL', 'Justin Bieber & Druski'), ('SOULFUL', 'Justin Bieber & Druski')], 0.99, 'pass'),
    ('SPEED DEMON', 'Justin Bieber',
     [('SPEED DEMON', 'Justin Bieber')], 1.0, 'pass'),
    ('STANDING ON BUSINESS', 'Justin Bieber',
     [('STANDING ON BUSINESS', 'Justin Bieber & Druski'), ('STANDING ON BUSINESS', 'Justin Bieber & Druski')], 0.96, 'pass'),
    ('STANDING ON BUSINESS', 'Justin Bieber',
     [('STANDING ON BUSINESS', 'Justin Bieber & Druski'), ('STANDING ON BUSINESS', 'Justin Bieber & Druski')], 0.99, 'pass'),
    ('STORY OF GOD', 'Justin Bieber',
     [('STORY OF GOD', 'Justin Bieber')], 0.97, 'pass'),
    ('SWAG', 'Justin Bieber',
     [('SWAG', 'Justin Bieber, Cash Cobain & Eddie Benjamin'), ('SWAG', 'Justin Bieber, Cash Cobain & Eddie Benjamin')], 0.97, 'skip'),
    ('SWAG', 'Justin Bieber',
     [('SWAG', 'Justin Bieber, Cash Cobain & Eddie Benjamin'), ('SWAG', 'Justin Bieber, Cash Cobain & Eddie Benjamin')], 0.97, 'skip'),
    ('SWEET SPOT', 'Justin Bieber',
     [('SWEET SPOT', 'Justin Bieber & Sexyy Red'), ('SWEET SPOT', 'Justin Bieber & Sexyy Red')], 1.0, 'pass'),
    ('SWEET SPOT', 'Justin Bieber',
     [('SWEET SPOT', 'Justin Bieber & Sexyy Red'), ('SWEET SPOT', 'Justin Bieber & Sexyy Red')], 0.99, 'pass'),
    ('THERAPY SESSION', 'Justin Bieber',
     [('THERAPY SESSION', 'Justin Bieber & Druski'), ('THERAPY SESSION', 'Justin Bieber & Druski')], 0.99, 'pass'),
    ('THERAPY SESSION', 'Justin Bieber',
     [('THERAPY SESSION', 'Justin Bieber & Druski'), ('THERAPY SESSION', 'Justin Bieber & Druski')], 0.99, 'pass'),
    ('THINGS YOU DO', 'Justin Bieber',
     [('THINGS YOU DO', 'Justin Bieber')], 0.96, 'pass'),
    ('THINGS YOU DO', 'Justin Bieber',
     [('THINGS YOU DO', 'Justin Bieber')], 0.99, 'pass'),
    ('TOO LONG', 'Justin Bieber',
     [('TOO LONG', 'Justin Bieber'), ('TOO LONG', 'Justin Bieber')], 0.97, 'pass'),
    ('TOO LONG', 'Justin Bieber',
     [('TOO LONG', 'Justin Bieber'), ('TOO LONG', 'Justin Bieber')], 0.97, 'pass'),
    ('WALKING AWAY', 'Justin Bieber',
     [('WALKING AWAY', 'Justin Bieber'), ('WALKING AWAY', 'Justin Bieber')], 0.81, 'pass'),
    ('WALKING AWAY', 'Justin Bieber',
     [('WALKING AWAY', 'Justin Bieber'), ('WALKING AWAY', 'Justin Bieber')], 0.81, 'pass'),
    ('WAY IT IS', 'Justin Bieber',
     [('WAY IT IS', 'Justin Bieber & Gunna')], 0.97, 'pass'),
    ('WAY IT IS', 'Justin Bieber',
     [('WAY IT IS', 'Justin Bieber & Gunna')], 0.99, 'pass'),
    ("WHEN IT'S OVER", 'Justin Bieber',
     [('WHEN IT’S OVER', 'Justin Bieber')], 0.97, 'pass'),
    ('WITCHYA', 'Justin Bieber',
     [('WITCHYA', 'Justin Bieber')], 0.97, 'pass'),
    ('YUKON', 'Justin Bieber',
     [('YUKON', 'Justin Bieber')], 0.97, 'pass'),
    ('YUKON', 'Justin Bieber',
     [('YUKON', 'Justin Bieber')], 0.99, 'pass'),
    ('ZUMA HOUSE', 'Justin Bieber',
     [('ZUMA HOUSE', 'Justin Bieber')], 0.97, 'pass'),
    ('ZUMA HOUSE', 'Justin Bieber',
     [('ZUMA HOUSE', 'Justin Bieber')], 0.99, 'pass'),
    ('Baby Be Mine', 'Michael Jackson',
     [('Monkey Business', 'Michael Jackson'), ('Baby Be Mine', 'Michael Jackson'), ('One Day in Your Life', 'Michael Jackson')], 0.96, 'pass'),
    ('Black or White', 'Michael Jackson',
     [('Black or White (single version)', 'Michael Jackson'), ('Black or White', 'Michael Jackson'), ('Black or White', 'Michael Jackson')], 0.97, 'pass'),
    ('Blood On The Dance Floor X Dangerous (The White Panda Mash-Up)', 'Michael Jackson',
     [('Blood on the Dance Floor X Dangerous (The White Panda Mash‐Up)', 'Michael Jackson')], 0.89, 'pass'),
    ('Blood on the Dance Floor', 'Michael Jackson',
     [('Blood on the Dance Floor', 'Michael Jackson'), ('Blood on the Dance Floor', 'Michael Jackson'), ('The Finer Things', 'Steve Winwood')], 0.98, 'pass'),
    ("Can't Let Her Get Away", 'Michael Jackson',
     [("Can't Let Her Get Away", 'Michael Jackson'), ('Can’t Let Her Get Away', 'Michael Jackson')], 0.97, 'pass'),
    ('Dangerous', 'Michael Jackson',
     [('Dangerous', 'Michael Jackson'), ('You Can’t Win', 'Michael Jackson'), ('Earth Song', 'Michael Jackson')], 0.98, 'pass'),
    ('Dangerous', 'Michael Jackson',
     [('Dangerous', 'Michael Jackson'), ('You Can’t Win', 'Michael Jackson'), ('Earth Song', 'Michael Jackson')], 0.98, 'pass'),
    ('Dirty Diana (2012 Remaster)', 'Michael Jackson',
     [('Dirty Diana', 'Michael Jackson'), ('Dirty Diana', 'Michael Jackson'), ('Dirty Diana', 'Michael Jackson')], 0.98, 'pass'),
    ('Ghosts', 'Michael Jackson',
     [('Ghost', 'Michael Jackson'), ('Jealous Ghost', 'Michael Jackson'), ('Ghosts', 'Michael Jackson')], 0.97, 'pass'),
    ('Give In to Me', 'Michael Jackson',
     [("Give In to Me (Michael Jackson's Vision)", 'Michael Jackson'), ('Give in to Me', 'Michael Jackson'), ('Give In to Me', 'Michael Jackson')], 0.98, 'pass'),
    ('Heal the World', 'Michael Jackson',
     [('Heal the World', 'Michael Jackson'), ('Heal the World (album version)', 'Michael Jackson'), ('Heal the World', 'Michael Jackson')], 0.92, 'pass'),
    ('In the Closet', 'Michael Jackson',
     [("In the Closet (Michael Jackson's Vision)", 'Michael Jackson'), ('Heal the World', 'Michael Jackson'), ('In the Closet', 'Michael Jackson')], 0.95, 'pass'),
    ('Jam', 'Michael Jackson',
     [("Don't Stop Till You Get Enough", 'Michael Jackson'), ('Jam', 'Michael Jackson'), ('Jam', 'Michael Jackson')], 0.95, 'pass'),
    ('Keep the Faith', 'Michael Jackson',
     [('Keep the Faith', 'Michael Jackson'), ('Keep the Faith', 'Michael Jackson'), ('Keep the Faith', 'Michael Jackson')], 0.98, 'pass'),
    ('Leave Me Alone (2012 Remaster)', 'Michael Jackson',
     [('Leave Me Alone', 'Michael Jackson'), ('Leave Me Alone', 'Michael Jackson'), ('Leave Me Alone', 'Michael Jackson')], 0.98, 'pass'),
    ('Remember the Time', 'Michael Jackson',
     [('Come Together', 'Michael Jackson'), ('She Drives Me Wild', 'Michael Jackson'), ('Remember the Time', 'Julian Vaughn')], 0.97, 'pass'),
    ('Scream', 'Michael Jackson',
     [('Scream', 'Blue Train'), ('Scream', 'Michael Jackson'), ('Scream (clean album version)', 'Michael Jackson & Janet Jackson')], 0.97, 'pass'),
    ('She Drives Me Wild', 'Michael Jackson',
     [('She Drives Me Wild', 'Michael Jackson'), ('The Girl Is Mine', 'Michael Jackson with Paul McCartney')], 0.96, 'pass'),
    ('Threatened', 'Michael Jackson',
     [('Threatened', 'Michael Jackson'), ('Threatened', 'Michael Jackson'), ('Threatened', 'Michael Jackson')], 0.99, 'pass'),
    ('Torture', 'Michael Jackson',
     [('Torture', 'The Jacksons'), ('Torture', 'The Jacksons'), ('Torture', 'The Jacksons')], 0.96, 'pass'),
    ('Unbreakable', 'Michael Jackson',
     [('Ben', 'Michael Jackson'), ('Unbreakable', 'Michael Jackson'), ('Unbreakable', 'Michael Jackson')], 0.99, 'pass'),
    ("Wanna Be Startin' Somethin'", 'Michael Jackson',
     [('Wanna Be Startin’ Somethin’', 'Michael Jackson'), ('Wanna Be Startin’ Somethin’', 'Michael Jackson'), ('Thriller', 'Michael Jackson')], 1.0, 'pass'),
    ('Who Is It', 'Michael Jackson',
     [('Who Is It', 'Michael Jackson'), ('Who Is It', 'Michael Jackson'), ('Who Is It', 'Michael Jackson')], 0.97, 'pass'),
    ('Why You Wanna Trip on Me', 'Michael Jackson',
     [('Why You Wanna Trip on Me', 'Michael Jackson')], 0.97, 'pass'),
    ('Will You Be There', 'Michael Jackson',
     [('Will You Be There', 'Michael Jackson'), ('Will You Be There', 'Michael Jackson'), ('Will You Be There (album version)', 'Michael Jackson')], 0.96, 'pass'),
    ('Xscape', 'Michael Jackson',
     [('Xscape', 'Michael Jackson'), ('Xscape', 'Michael Jackson')], 0.99, 'pass'),
    ('Memory Reboot', 'VØJ',
     [('Memory Reboot', 'VØJ & Narvent')], 1.0, 'skip'),
    ('Memory Reboot', 'VØJ',
     [('Memory Reboot', 'VØJ & Narvent')], 0.98, 'skip'),
]

# AcoustID answers these fingerprints with a DIFFERENT artist's recording of the
# same title (a metal cover). Titles match, artists do not, so the one thing the
# core must not do is call them verified — they belong in the review queue, not
# in the invariant above.
_AMBIGUOUS_LOOKUPS = [
    ("Somebody's Watching Me (Single Version)", 'Michael Jackson',
     [('Somebody’s Watching Me', 'Rockwell'), ("Somebody's Watching Me (Single Version)", 'Rockwell'), ('Somebody’s Watching Me', 'Gene Rockwell')], 0.98, 'fail'),
]

_CLEAN_LOOKUPS = _REAL_LOOKUPS


@pytest.mark.parametrize("title,artist,recordings,score,decision", _CLEAN_LOOKUPS)
def test_a_correct_file_is_never_quarantined(title, artist, recordings, score, decision):
    """Every row is a file that IS what it claims, so none may come back FAIL."""
    out = evaluate(
        title, artist,
        [{'title': t, 'artist': a} for t, a in recordings],
        fingerprint_score=score,
        aliases_provider=_aliases_for(artist),
    )
    assert out.decision != Decision.FAIL, (
        f"{title!r} by {artist!r} was quarantined against {out.matched_title!r} "
        f"by {out.matched_artist!r} (title_sim={out.title_sim:.2f}, "
        f"artist_sim={out.artist_sim:.2f}): {out.reason}"
    )


@pytest.mark.parametrize("title,artist,recordings,score,decision",
                         _REAL_LOOKUPS + _AMBIGUOUS_LOOKUPS)
def test_the_decision_on_real_data_does_not_drift(title, artist, recordings, score, decision):
    """Golden master: PASS must not quietly become SKIP either. 'Not FAIL' alone
    would let a threshold change silently stop verifying half the library."""
    out = evaluate(
        title, artist,
        [{'title': t, 'artist': a} for t, a in recordings],
        fingerprint_score=score,
        aliases_provider=_aliases_for(artist),
    )
    assert out.decision.value == decision, (
        f"{title!r}: {decision} -> {out.decision.value} ({out.reason})"
    )


@pytest.mark.parametrize("title,artist,recordings,score,decision", _AMBIGUOUS_LOOKUPS)
def test_a_foreign_cover_of_the_same_title_is_not_a_pass(title, artist, recordings,
                                                         score, decision):
    out = evaluate(
        title, artist,
        [{'title': t, 'artist': a} for t, a in recordings],
        fingerprint_score=score,
        aliases_provider=_aliases_for(artist),
    )
    assert out.decision != Decision.PASS


# --- the version-tail rule, on real catalogue titles ---
#
# One representative per distinct tail shape found in the 13,728-title corpus.
# A version tail must leave the title scoring 1.00 against its own bare form —
# that is exactly what AcoustID sees when the provider labels the version and
# MusicBrainz returns the plain recording title.

_TAIL_PAIRS = [
    ('Dinata Dinata - C&N Project Mix', 'Dinata Dinata'),
    ("Cat's Eye Main Theme - Chill Ver.", "Cat's Eye Main Theme"),
    ('1106 TYBW CH united - Cover', '1106 TYBW CH united'),
    ('Above and Beyoncé - Dance Mixes', 'Above and Beyoncé'),
    ("Don't You Want Me - Delta Heavy Remix", "Don't You Want Me"),
    ('DARK ARIA <LV.2> - Solo Leveling S2 - Emotional Cover', 'DARK ARIA <LV.2> - Solo Leveling S2'),
    ('HIS THEME - Undertale - Emotional Version', 'HIS THEME - Undertale'),
    ('#tBt - EP', '#tBt'),
    ('10th S-RANK HUNTER - aikari - Solo Leveling S2 - Epic Cover', '10th S-RANK HUNTER - aikari - Solo Leveling S2'),
    ('AIZO - Jujutsu Kaisen S3 Opening Song - Epic Version', 'AIZO - Jujutsu Kaisen S3 Opening Song'),
    ('How Does It Feel - Extended', 'How Does It Feel'),
    ('Edge of Desire - Extended Mix', 'Edge of Desire'),
    ('Kataomoi - From THE FIRST TAKE', 'Kataomoi'),
    ("Cat's Eye Theme: Smooth set - Inst Ver.", "Cat's Eye Theme: Smooth set"),
    ('HEADSHOT - Instrumental', 'HEADSHOT'),
    ('I really want to stay at your house - EPIC VERSION - Instrumental ver.', 'I really want to stay at your house - EPIC VERSION'),
    ('Runaway Love - Kanye West Remix', 'Runaway Love'),
    ('Blumenkranz -Karaoke Version', 'Blumenkranz'),
    ('Bring Me Home - Live 2011', 'Bring Me Home'),
    ('Bee Gees - Lay it On Me - Live American Broadcast', 'Bee Gees - Lay it On Me'),
    ('The Great James Brown - Live At The Apollo 1995', 'The Great James Brown'),
    ('Sixth Magnitude Star - MHA Arr Ver.', 'Sixth Magnitude Star'),
    ('Funkot Dance! - Sexy Hyper Dance Party - Michael Jackson Mix', 'Funkot Dance! - Sexy Hyper Dance Party'),
    ('Groovejet - not without friends Extended Remix', 'Groovejet'),
    ('S_Team -Orchestra Version', 'S_Team'),
    ('WISHING - To Be Hero X Ep 9 OST - Piano & Orchestra Version', 'WISHING - To Be Hero X Ep 9 OST'),
    ('Vogel im Käfig - Rain Version', 'Vogel im Käfig'),
    ('the boy is mine – Remix', 'the boy is mine'),
    ('Can You Feel It - Remixes', 'Can You Feel It'),
    ('10,000 Hours - Single', '10,000 Hours'),
    ('GLOW - Slowed', 'GLOW'),
    ('School Rooftop - Slowed Down Version', 'School Rooftop'),
    ('MY COLOR - To Be Hero X Ep 10 OST - Soft Piano Version', 'MY COLOR - To Be Hero X Ep 10 OST'),
    ('12" Masters - The Essential Mixes', '12" Masters'),
    ('That Acid - The Remixes', 'That Acid'),
    ('Superman Theme - Trailer Version', 'Superman Theme'),
    ('Before My Body Is Dry - Version', 'Before My Body Is Dry'),
]


@pytest.mark.parametrize("full,bare", _TAIL_PAIRS)
def test_a_real_version_tail_still_matches_the_bare_recording(full, bare):
    assert similarity(full, bare) == 1.0, (
        f"{full!r} no longer matches its own bare title {bare!r}"
    )


# --- CJK identity must survive normalization ---

_CJK_TITLES = [
    '"Attack On Titan" Season 3 (Original Soundtrack) = 「進撃の巨人」Season 3 オリジナルサウンドトラック',
    '"Attack on Titan" Original Soundtrack II = 「進撃の巨人」オリジナルサウンドトラックII',
    '86―エイティシックス― オリジナル・サウンドトラック',
    'AOTs2M他1',
    'AOTs2M他2',
    'AOTs2M他3',
    'AOTs2M他4',
    'Ashes on The Fire (進撃の巨人 The Final Season Original Soundtrack) - Single',
    'Dream Diver (feat. 巡音ルカ)',
    'ERENthe標',
    'E・M・A',
    'Feeling Good (Live典藏)',
    'Home (Live典藏)',
    'Invisible (feat. 雪歌ユフ)',
    'LilaS (feat. たかはしほのか) - Single',
    'MYTH & ROID Concept mini album 〈Episode 2〉『VERDE』',
    'MYTH & ROID ベストアルバム「MUSEUM-THE BEST OF MYTH & ROID-」',
    'Make It Right (feat. 滲音かこい)',
    'Might+U 〜Live Special Arrange〜＜My Hero Academia＞',
    'Music Box (feat. 初音ミク)',
    'My War (Attack on Titan) - 僕の戦争',
    'My War (Attack on Titan) - 僕の戦争 - Single',
    'Nightmare (feat. 破壊音マイコ)',
    'Puzzle Heart (feat. 初音ミク)',
    'TVアニメ「Re:ゼロから始める異世界生活」後期オープニングテーマ「Paradisus-Paradoxum」',
    'TVアニメ「オーバーロード」エンディングテーマ「L.L.L.」',
    'TVアニメ「キングダム」第4シリーズ Original Sound Track',
    'TVアニメ「ブブキ・ブランキ」エンディングテーマ「ANGER/ANGER」',
    'TVアニメ「七つの大罪 憤怒の審判」オリジナル・サウンドトラック',
    'TVアニメ「七つの大罪 神々の逆鱗」オリジナル・サウンドトラック',
    'TVアニメ「進撃の巨人 The Final Season」 Original Sound Track Complete Album',
    'TVアニメ「進撃の巨人」 The Final Season Original Soundtrack',
    'TVアニメ「進撃の巨人」 The Final Season Original Soundtrack 02',
    'TVアニメ「進撃の巨人」 The Final Season Original Soundtrack 03 - EP',
    'TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック',
    'TVアニメ「進撃の巨人」オリジナルサウンドトラック',
    'Take Me Home，Country Roads (Live典藏)',
    'Thunderbolt Fantasy 東離劍遊紀 オリジナルサウンドトラック',
    'Thunderbolt Fantasy 東離劍遊紀2 オリジナルサウンドトラック',
    'attack音 & DERENthe標 (From "Shingeki no Kyojin 2")',
    'attack音D',
    'cóunter・attàck-mˈænkάɪnd',
    '「CRISIS 公安機動捜査隊特捜班」ORIGINAL SOUNDTRACK/BONUS TRACK',
    '「プロメア」オリジナルサウンドトラック',
    '「進撃の巨人」Season3 オリジナルサウンドトラック',
    'もうどうなってもいいや - Single',
    'アルドノア・ゼロ オリジナル・サウンドトラック',
    'キルラキル コンプリートサウンドトラック',
    'キルラキルオリジナルサウンドトラック',
    'ギルティクラウン オリジナルサウンドトラック',
    '一个人跳舞 (Live)',
    '一个人跳舞 (Live典藏)',
    '七つの大罪 オリジナル・サウンドトラック',
    '七つの大罪 オリジナル・サウンドトラック 2',
    '二千年... 若しくは... 二万年後の君へ・・・ - Single',
    '侠客令 (Live)',
    '侠客令 (Live典藏)',
    '俺だけレベルアップな件 Original Soundtrack',
    '俺だけレベルアップな件 Season2 -Arise from the Shadow- Original Soundtrack',
    '僕の戦争 - Single',
    '凸】♀】♂】←Titan',
    '劇場版 七つの大罪 天空の囚われ人 オリジナル・サウンドトラック',
    '劇場版「進撃の巨人」前編~紅蓮の弓矢~エンディングテーマ YAMANAIAME produced by 澤野弘之',
    '劇場版「進撃の巨人」後編~自由の翼~エンディングテーマ theDOGS produced by 澤野弘之 - EP',
    '悪魔の子 - Single',
    '最後の巨人 - Single',
    '未来的主人翁 (Live)',
    '未来的主人翁 (Live典藏)',
    '欢乐时光：派对狂欢中的平静时刻',
    '歌手2025 第6期 (Live)',
    '甲鉄城のカバネリ COMPLETE SOUNDTRACK',
    '甲鉄城のカバネリ ORIGINAL SOUNDTRACK',
    '群青のファンファーレ オリジナル・サウンドトラック',
    '自由への進撃 - Single',
    '進撃gt20130218巨人',
    '進撃pf-adlib-b20130218巨人',
    '進撃pf-adlib-c20130218巨人',
    '進撃pf-medley20130629巨人',
    '進撃pf20130218巨人',
    '進撃st-hrn-egt20130629巨人',
    '進撃st-hrn-gt-pf20130629巨人',
    '進撃st-hrn-gt20130629巨人',
    '進撃st20130629巨人',
    '進撃vc-pf20130218巨人',
    '進撃vn-pf20130524巨人',
    '進撃の軌跡',
    '雪を聴く夜',
    '青の祓魔師 オリジナル・サウンドトラック 2024-25',
    '青の祓魔師 オリジナル・サウンドトラック I',
    '青の祓魔師 オリジナル・サウンドトラック II',
    '青の祓魔師 京都不浄王篇 オリジナル・サウンドトラック',
    '青の祓魔師 劇場版 オリジナル・サウンドトラック',
]


@pytest.mark.parametrize("title", _CJK_TITLES)
def test_cjk_titles_normalize_to_something_comparable(title):
    assert normalize(title), f"{title!r} normalized to an empty string"
