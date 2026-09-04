"""Sharing an overlay template into a chat room.

Boulder's idea: not a file attachment, a native one. Pick a template, it rides
the message, and the person reading can click it straight into their own Overlay
Studio. Plus the constraint that shapes the whole thing - "there are some pieces
of the overlay that the user must manually download to use."

Those pieces are the IMAGES. An image layer's src is `asset://<sha1>.<ext>`,
uploaded to the SENDER's install. The definition travels; the bytes do not. The
ref being content-addressed is what saves this: the name identifies exactly which
file is missing, and a recipient who already has those bytes resolves it for
free. So an import can say "this needs two images you do not have" rather than
quietly rendering a template with holes in it.

Two structural facts decided the design:

* The definition CANNOT ride in the protocol object. protocol_of allows one
  level of nesting and a template is layers-of-objects, so it would be rejected
  outright. It gets its own envelope key, exactly as file cards and reactions do.
* A chat message caps at 2000 encoded characters. zlib carries a realistic
  template of about ten layers; a bigger one has to be refused with a reason,
  not truncated into something that renders wrong.

Everything here arrives from a PUBLIC ROOM and is handed to a poster renderer,
so it is bounded in every direction a renderer could be hurt by.
"""

from __future__ import annotations

import json

import pytest

from core import chat_codec

# the room-send fixture lives with the other chat API tests
from tests.test_chat_api import chat_app  # noqa: F401


def _layer(i=0, **kw):
    d = {"id": "l%d" % i, "type": "text", "text": "{resolution}",
         "x": 0.1, "y": 0.9, "font": "Inter", "size": 0.04}
    d.update(kw)
    return d


def _defn(layers=None, **kw):
    d = {"version": 1, "canvas": {"w": 1000, "h": 1500},
         "layers": layers if layers is not None else [_layer()]}
    d.update(kw)
    return d


def _env(name="Corner badge", defn=None):
    return {"v": 1, "t": "", "o": {"n": name, "d": defn if defn is not None else _defn()}}


# ── the happy path ───────────────────────────────────────────────────────────
def test_a_template_survives_the_envelope():
    out = chat_codec.overlay_of(_env())
    assert out["n"] == "Corner badge"
    assert out["d"]["layers"][0]["text"] == "{resolution}"


def test_a_real_template_fits_a_chat_message():
    """The whole feature depends on this. Ten varied layers is a normal
    template and it has to survive the 2000 character wire limit."""
    defn = _defn([_layer(i, name="Layer %d" % i, color="#ffcc0%d" % (i % 10))
                  for i in range(10)])
    packed = chat_codec.encode("", {"o": {"n": "Ten layers", "d": defn}})
    assert packed is not None
    assert len(packed) <= chat_codec.MAX_ENCODED_LEN
    assert chat_codec.overlay_of(chat_codec.decode(packed))["d"]["layers"][0]["id"] == "l0"


def test_an_oversized_template_is_refused_by_encode_not_truncated():
    """A truncated definition would render as a broken template rather than as
    a failure, so encode returns None and the caller has to say so.

    The layers are deliberately VARIED. zlib flattens repetition so completely
    that sixty identical layers still fit the wire - which is exactly why a
    real template of about fifteen mixed layers is the actual ceiling, and why
    a size test built on copy-pasted content proves nothing.
    """
    import random
    import string
    rnd = random.Random(11)

    def noise(n):
        return "".join(rnd.choice(string.ascii_letters + string.digits) for _ in range(n))

    huge = _defn([_layer(i, name=noise(60), text=noise(60), color="#" + noise(6),
                         src="asset://" + noise(16) + ".png")
                  for i in range(40)])
    assert chat_codec.encode("", {"o": {"n": "Huge", "d": huge}}) is None


# ── shape: it must be a renderable template ──────────────────────────────────
def test_no_overlay_key_is_simply_not_a_share():
    assert chat_codec.overlay_of({"v": 1, "t": "hello"}) is None
    assert chat_codec.overlay_of({"v": 1, "t": "", "o": "nope"}) is None
    assert chat_codec.overlay_of(None) is None


def test_a_nameless_share_is_refused():
    """A card with no title is a card nobody can decide about."""
    assert chat_codec.overlay_of(_env(name="   ")) is None


def test_a_definition_with_no_layers_paints_nothing():
    for bad in ({"version": 1}, {"layers": []}, {"layers": "text"}, {"layers": {}}):
        assert chat_codec.overlay_of({"o": {"n": "X", "d": bad}}) is None


def test_layers_must_be_objects():
    assert chat_codec.overlay_of({"o": {"n": "X", "d": {"layers": ["text", 3]}}}) is None


def test_the_definition_must_be_an_object():
    for bad in ("layers", ["layers"], 7, None):
        assert chat_codec.overlay_of({"o": {"n": "X", "d": bad}}) is None


# ── hostile input: bounded in every direction ────────────────────────────────
def test_too_many_layers_is_refused():
    over = _defn([_layer(i) for i in range(chat_codec.OVERLAY_MAX_LAYERS + 1)])
    assert chat_codec.overlay_of({"o": {"n": "X", "d": over}}) is None


def test_a_serialized_definition_over_the_ceiling_is_refused():
    big = _defn([_layer(0, blob="z" * 500) for _ in range(40)])
    assert len(json.dumps(big)) > chat_codec.OVERLAY_MAX_JSON
    assert chat_codec.overlay_of({"o": {"n": "X", "d": big}}) is None


def test_a_string_over_the_cap_is_refused():
    assert chat_codec.overlay_of(
        {"o": {"n": "X", "d": _defn([_layer(0, text="z" * 513)])}}) is None


def test_nesting_deeper_than_a_template_needs_is_refused():
    """A layer with a bg and a shadow is three levels. Anything past that is
    not a template, it is someone probing the parser."""
    deep = _defn([_layer(0, a={"b": {"c": {"d": {"e": 1}}}})])
    assert chat_codec.overlay_of({"o": {"n": "X", "d": deep}}) is None


def test_nan_and_infinity_never_reach_a_renderer():
    """They survive some json encoders and turn into a renderer dividing by
    nothing."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert chat_codec.overlay_of(
            {"o": {"n": "X", "d": _defn([_layer(0, size=bad)])}}) is None


def test_an_absurd_number_is_refused():
    assert chat_codec.overlay_of(
        {"o": {"n": "X", "d": _defn([_layer(0, x=1e30)])}}) is None


def test_too_many_keys_on_one_object_is_refused():
    fat = _layer(0)
    fat.update({"k%d" % i: i for i in range(chat_codec.OVERLAY_MAX_KEYS + 1)})
    assert chat_codec.overlay_of({"o": {"n": "X", "d": _defn([fat])}}) is None


def test_the_name_is_capped_and_normalised_not_rejected():
    """A long or messily-spaced name is a cosmetic problem, not an attack."""
    out = chat_codec.overlay_of(_env(name="  Lots   of\n spaces  "))
    assert out["n"] == "Lots of spaces"
    long_name = chat_codec.overlay_of(_env(name="z" * 400))
    assert len(long_name["n"]) == chat_codec.OVERLAY_MAX_NAME


# ── the pieces that do not travel ────────────────────────────────────────────
def test_every_asset_reference_is_reported():
    defn = _defn([
        _layer(0, type="image", src="asset://aaaaaaaaaaaaaaaa.png"),
        _layer(1, type="logo", src="asset://bbbbbbbbbbbbbbbb.png"),
    ])
    assert chat_codec.overlay_assets(defn) == [
        "asset://aaaaaaaaaaaaaaaa.png", "asset://bbbbbbbbbbbbbbbb.png"]


def test_the_same_asset_used_twice_is_listed_once():
    """The import prompt should say one missing image, not two."""
    defn = _defn([_layer(0, src="asset://same.png"), _layer(1, src="asset://same.png")])
    assert chat_codec.overlay_assets(defn) == ["asset://same.png"]


def test_assets_are_found_wherever_they_hide_in_the_definition():
    defn = _defn([_layer(0, bg={"image": "asset://nested.png"})],
                 canvas={"w": 1, "h": 1, "bg": "asset://canvas.png"})
    found = chat_codec.overlay_assets(defn)
    assert "asset://nested.png" in found and "asset://canvas.png" in found


def test_a_template_with_no_images_needs_nothing_downloaded():
    """Which is the common case, and it must not be dressed up as a warning."""
    assert chat_codec.overlay_assets(_defn()) == []


def test_a_plain_url_is_not_an_asset_reference():
    defn = _defn([_layer(0, src="https://example.com/logo.png")])
    assert chat_codec.overlay_assets(defn) == []


def test_asset_scanning_never_raises_on_junk():
    for junk in (None, "text", 7, [], {}, {"layers": None}):
        assert chat_codec.overlay_assets(junk) == []


# ── the round trip ───────────────────────────────────────────────────────────
def test_share_then_receive_reproduces_the_design_exactly():
    defn = _defn([_layer(0, src="asset://cafe0123cafe0123.png", type="image"),
                  _layer(1, text="4K")])
    packed = chat_codec.encode("", {"o": {"n": "Badge pack", "d": defn}})
    got = chat_codec.overlay_of(chat_codec.decode(packed))
    assert got["n"] == "Badge pack"
    assert got["d"] == defn
    assert chat_codec.overlay_assets(got["d"]) == ["asset://cafe0123cafe0123.png"]


def test_a_share_carries_no_visible_text_but_still_decodes():
    """The card IS the message. A vanilla Soulseek client sees the envelope as
    line noise, which is the same bargain every other rich message makes."""
    packed = chat_codec.encode("", {"o": {"n": "X", "d": _defn()}})
    dec = chat_codec.decode(packed)
    assert dec["t"] == ""
    assert chat_codec.overlay_of(dec) is not None


# ── end to end: the send endpoint really builds a card ───────────────────────
#
# Everything above tests the codec in isolation. This drives the actual
# /api/chat/room/message endpoint and inspects the bytes that would go on the
# wire, because the interesting failures live in the wiring, not the validator.

def test_the_send_endpoint_puts_a_decodable_template_on_the_wire(chat_app):
    http, state = chat_app
    defn = _defn([_layer(0, text="4K"), _layer(1, type="image",
                                               src="asset://cafe0123cafe0123.png")])

    r = http.post("/api/chat/room/message",
                  json={"message": "", "overlay": {"n": "Corner badge", "d": defn}})
    assert r.status_code == 200, r.get_json()

    # what a vanilla Soulseek client would literally receive
    room, wire = state["client"].sent_room[-1]
    assert wire.startswith(chat_codec.MARKER)

    # ...and what a SoulSync client makes of it
    dec = chat_codec.decode(wire)
    share = chat_codec.overlay_of(dec)
    assert share["n"] == "Corner badge"
    assert share["d"] == defn
    assert chat_codec.overlay_assets(share["d"]) == ["asset://cafe0123cafe0123.png"]


def test_a_share_that_cannot_fit_is_refused_by_the_endpoint(chat_app):
    """And the error names the TEMPLATE, not the message: with a template
    attached the text is almost never the problem, and "message too long" sends
    someone to shorten a sentence that was already empty."""
    import random
    import string
    rnd = random.Random(3)

    def noise(n):
        return "".join(rnd.choice(string.ascii_letters + string.digits) for _ in range(n))

    huge = _defn([_layer(i, name=noise(60), text=noise(60), src="asset://" + noise(16))
                  for i in range(40)])
    http, state = chat_app
    before = len(state["client"].sent_room)

    r = http.post("/api/chat/room/message",
                  json={"message": "", "overlay": {"n": "Huge", "d": huge}})
    assert r.status_code == 400
    err = r.get_json()["error"]
    assert "template" in err.lower() and "40 layers" in err
    assert len(state["client"].sent_room) == before, "a refused share still hit the room"


def test_a_malformed_overlay_is_refused_before_anything_is_sent(chat_app):
    http, state = chat_app
    before = len(state["client"].sent_room)
    for bad in ({"n": "", "d": _defn()}, {"n": "X", "d": {"layers": []}}, {"n": "X"}):
        r = http.post("/api/chat/room/message", json={"message": "", "overlay": bad})
        assert r.status_code == 400, bad
    assert len(state["client"].sent_room) == before


def test_an_ordinary_message_is_untouched_by_any_of_this(chat_app):
    """The overlay path must not become a tax on every message."""
    http, state = chat_app
    r = http.post("/api/chat/room/message", json={"message": "hello"})
    assert r.status_code == 200
    dec = chat_codec.decode(state["client"].sent_room[-1][1])
    assert dec["t"] == "hello"
    assert chat_codec.overlay_of(dec) is None
