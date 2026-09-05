"""The two protocol validators must agree, and now a corpus proves it.

core/chat_codec.protocol_of (python) and ChatProtocol.parseProtocol
(chat-protocol.js) gate the SAME message bus from opposite ends. Python decides
what a client may SEND and what it is handed on RECEIVE; the JS decides what the
client acts on. The codec docstring already said they "must agree or clients
desync" — but nothing checked, and they had drifted:

    python:  abs(v) < 1e15
    js:      isFinite(v)

So anything from 1e15 up was accepted by a client's own validator and refused by
the server: a payload you built would come back a 400, and an inbound carrier
python dropped never reached the bus at all.

This runs one corpus through the python half. The JS half runs the SAME file in
webui/src/test/chat-protocol-parity.test.ts. A rule added to one validator and
not the other now fails a test instead of desyncing a room.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import chat_codec

CORPUS = json.loads(
    (Path(__file__).resolve().parent / "data" / "chat_protocol_corpus.json")
    .read_text(encoding="utf-8"))


def _cases(bucket):
    return [pytest.param(c["p"], id=c["why"][:60]) for c in CORPUS[bucket]]


@pytest.mark.parametrize("p", _cases("accept"))
def test_python_accepts_what_the_js_accepts(p):
    assert chat_codec.protocol_of({"p": p}) is not None


@pytest.mark.parametrize("p", _cases("reject"))
def test_python_rejects_what_the_js_rejects(p):
    assert chat_codec.protocol_of({"p": p}) is None


def test_the_corpus_covers_both_sides_of_every_cap():
    """A corpus of only-valid or only-invalid cases would pass while proving
    nothing about where the line actually is."""
    assert len(CORPUS["accept"]) >= 10
    assert len(CORPUS["reject"]) >= 15
    whys = " ".join(c["why"] for c in CORPUS["accept"] + CORPUS["reject"]).lower()
    for cap in ("24 char", "512", "sixteen", "32", "nesting", "magnitude"):
        assert cap in whys, f"the corpus never exercises the {cap} cap"
