"""the hero billboard never features a grey silhouette as a photo.

sept 29 2026: deezer answers "no photo" with a real url whose hash is the md5
of an empty string. the hero passed it straight through, so coldplay sat on
the discover billboard as a grey head, and the rotation dots did too.
"""

from core.discovery.hero import billboard_order

DEEZER_BLANK = ('https://cdn-images.dzcdn.net/images/artist/'
                'd41d8cd98f00b204e9800998ecf8427e/1000x1000-000000-80-0-0.jpg')
REAL = 'https://cdn-images.dzcdn.net/images/artist/d62a818a5de6455f17b6a992cf22b32f/1000x1000.jpg'


def _a(name, img):
    return {'artist_name': name, 'image_url': img}


def test_a_placeholder_url_is_no_photo():
    out = billboard_order([_a('Coldplay', DEEZER_BLANK)])
    assert out[0]['image_url'] is None


def test_the_empty_path_placeholder_is_no_photo_either():
    out = billboard_order([_a('X', 'https://cdn-images.dzcdn.net/images/artist//250x250.jpg')])
    assert out[0]['image_url'] is None


def test_real_photos_lead_and_the_rotation_order_holds_inside_each_group():
    out = billboard_order([
        _a('no photo 1', DEEZER_BLANK),
        _a('photo 1', REAL),
        _a('no photo 2', None),
        _a('photo 2', 'https://i.scdn.co/image/abc'),
    ])
    assert [a['artist_name'] for a in out] == ['photo 1', 'photo 2', 'no photo 1', 'no photo 2']


def test_a_real_photo_is_left_alone():
    assert billboard_order([_a('Pink Floyd', REAL)])[0]['image_url'] == REAL
