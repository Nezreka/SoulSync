"""CT watch — DESIGN-CONNECT v0 section 5.

The one real trust cost of the brokered-certificate design: whoever
controls the connect service's DNS-01 could mint a certificate for a
server's name. The mitigation lives here, on the server: each server
watches Certificate Transparency for its own name
(``*.<dns_id>.connect.ssync.net``) and flags any certificate it did
not issue itself. Own apps are protected by the pinned server key
regardless; this watch is what protects TLS-only third-party clients
and what tells the admin something is wrong.

v0 source: crt.sh's JSON API. A watch result is a list of unexpected
entries (serial/issuer/name) for the caller to log and surface; the
check itself is a pure comparison against the serials this server
knows are its own.
"""
from __future__ import annotations

import logging

log = logging.getLogger("soulsync.connect.ctwatch")

CRT_SH_URL = "https://crt.sh/?q=%25.{dns_id}.connect.ssync.net&output=json"


def find_unexpected(entries: list[dict], known_serials: set[str]) -> list[dict]:
    """crt.sh entries whose serial this server does not recognise.
    Serials compare as lowercase hex without separators."""
    unexpected = []
    for entry in entries:
        serial = str(entry.get("serial_number", "")).replace(":", "").lower()
        if serial and serial not in known_serials:
            unexpected.append(
                {
                    "serial_number": serial,
                    "issuer_name": entry.get("issuer_name"),
                    "common_name": entry.get("common_name"),
                    "not_before": entry.get("not_before"),
                    "not_after": entry.get("not_after"),
                }
            )
    return unexpected


def check_ct_log(dns_id_value: str, known_serials: set[str], http_get) -> list[dict]:
    """Fetch the CT log for this server's name and return the
    unexpected certificates (empty list = only our own certs exist).
    http_get(url) -> object with .json(). Raises on fetch failure:
    a watch that cannot see the log must say so, not pass silently."""
    resp = http_get(CRT_SH_URL.format(dns_id=dns_id_value))
    entries = resp.json()
    unexpected = find_unexpected(entries, known_serials)
    for entry in unexpected:
        log.error(
            "connect CT watch: UNEXPECTED certificate for *.%s.connect.ssync.net: serial %s issued by %s",
            dns_id_value,
            entry["serial_number"],
            entry["issuer_name"],
        )
    return unexpected
