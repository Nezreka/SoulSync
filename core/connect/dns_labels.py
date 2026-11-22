"""IP-to-hostname label encoding — DESIGN-CONNECT v0 section 5.

The connect DNS decodes a server's IP straight from the hostname label;
nothing is stored. Two label forms under ``*.<lookup_id>``:

* IPv4: the dashed dotted quad — ``192-168-1-50`` — answered with A.
* IPv6: the 16 address bytes as one 32-char lowercase hex label —
  answered with AAAA.

Encoding is canonical: IPv4 parts are decimal with no leading zeros;
IPv6 text form is whatever ``ipaddress`` produces (compressed). Decoding
is strict: anything that is not exactly one of the two canonical forms
is malformed (the DNS answers NXDOMAIN; a well-formed name asked for the
other record type gets NODATA — that distinction lives in the DNS
layer, not here).
"""
from __future__ import annotations

import ipaddress
import re

_V4_LABEL = re.compile(r"^(\d{1,3})-(\d{1,3})-(\d{1,3})-(\d{1,3})$")
_V6_LABEL = re.compile(r"^[0-9a-f]{32}$")


def encode_ipv4_label(ip: str) -> str:
    addr = ipaddress.IPv4Address(ip)  # raises on malformed input
    return str(addr).replace(".", "-")


def decode_ipv4_label(label: str) -> str:
    m = _V4_LABEL.match(label)
    if not m:
        raise ValueError("not an IPv4 label")
    parts = m.groups()
    for part in parts:
        if len(part) > 1 and part.startswith("0"):
            raise ValueError("IPv4 label parts have no leading zeros")
        if int(part) > 255:
            raise ValueError("IPv4 label part out of range")
    return ".".join(parts)


def encode_ipv6_label(ip: str) -> str:
    addr = ipaddress.IPv6Address(ip)  # raises on malformed input
    return addr.packed.hex()


def decode_ipv6_label(label: str) -> str:
    if not _V6_LABEL.match(label):
        raise ValueError("not an IPv6 label")
    return str(ipaddress.IPv6Address(bytes.fromhex(label)))


def decode_label(label: str) -> tuple[str, str]:
    """Decode either form: returns ("A", ipv4) or ("AAAA", ipv6).
    Raises ValueError on anything malformed."""
    if _V6_LABEL.match(label):
        return ("AAAA", decode_ipv6_label(label))
    return ("A", decode_ipv4_label(label))
