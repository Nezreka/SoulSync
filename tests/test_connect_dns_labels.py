"""the DNS label codec, pinned against the shared conformance vectors:
IPv4 as a dashed quad, IPv6 as 32 lowercase hex chars, strict decode."""
from __future__ import annotations

import pytest

from core.connect import dns_labels

IPV4 = [("192.168.1.50", "192-168-1-50"), ("203.0.113.7", "203-0-113-7"), ("0.0.0.0", "0-0-0-0")]
IPV6 = [
    ("2001:db8::1", "20010db8000000000000000000000001"),
    ("::1", "00000000000000000000000000000001"),
    ("fe80::a634:d9ff:fe07:c5a8", "fe80000000000000a634d9fffe07c5a8"),
]
MALFORMED = [
    "192-168-01-50", "192-168-1-256", "192-168-1", "192.168.1.50",
    "20010DB8000000000000000000000001", "gggggggggggggggggggggggggggggggg",
    "", "1-2-3-4-5",
]


def test_ipv4_labels():
    for ip, label in IPV4:
        assert dns_labels.encode_ipv4_label(ip) == label
        assert dns_labels.decode_label(label) == ("A", ip)


def test_ipv6_labels():
    for ip, label in IPV6:
        assert dns_labels.encode_ipv6_label(ip) == label
        assert dns_labels.decode_label(label) == ("AAAA", ip)


def test_malformed_labels_raise():
    for bad in MALFORMED:
        with pytest.raises(ValueError):
            dns_labels.decode_label(bad)
