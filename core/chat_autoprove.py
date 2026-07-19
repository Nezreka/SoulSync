"""Auto-answer 'prove you are human' PM challenges from Soulseek uploaders.

Some uploaders run anti-leech bots that queue-block you until you type a
token in chat ("ProveIt: ... please type \"human\" in this chat to be added
to my whitelist"). Downloads then sit blocked forever unless someone notices
the PM. This scans unread private messages for that challenge shape, replies
with the demanded token, and acknowledges the conversation — so overnight
grabs don't die to a bot bouncer.

Deliberately conservative:
  - PMs only, inbound ('In') UNACKNOWLEDGED messages only — a message Boulder
    already read in the chat UI never triggers a reply
  - the token must be a single short safe word ([A-Za-z0-9_-]{1,24}) matched
    by one of the known challenge phrasings; anything else is ignored
  - one reply per user per cooldown window (challenges repeat hourly; the
    whitelist add only needs one answer)
  - every reply is returned to the caller so it can be logged + notified —
    an invisible auto-responder is indistinguishable from a broken one
"""

from __future__ import annotations

import re
import time

from utils.logging_config import get_logger

logger = get_logger("chat.autoprove")

# One reply per user per window. Challenge bots repeat hourly; re-answering
# every repeat looks like the very bot behaviour the challenge exists to catch.
DEFAULT_COOLDOWN_S = 12 * 3600

# Known challenge phrasings. Each captures the demanded token — the reply is
# whatever the bot asked for, not a hardcoded "human".
_PATTERNS = (
    # 'please type "human" in this chat to be added to my whitelist'
    re.compile(r"""type\s+["']?([A-Za-z0-9_-]{1,24})["']?\s+in\s+(?:this\s+)?chat""", re.I),
    # 'reply with "human" to be whitelisted' / 'reply human to get access'
    re.compile(r"""reply\s+(?:with\s+)?["']?([A-Za-z0-9_-]{1,24})["']?\s+to\s+(?:be|get)""", re.I),
    # 'say "human" in chat' / 'say human to be added'
    re.compile(r"""say\s+["']?([A-Za-z0-9_-]{1,24})["']?\s+(?:in|to)\b""", re.I),
)


def extract_token(text) -> str | None:
    """The demanded reply token, or None when the message isn't a challenge."""
    s = str(text or "")
    for pat in _PATTERNS:
        m = pat.search(s)
        if m:
            return m.group(1)
    return None


def _messages_of(convo) -> list:
    """Tolerate both slskd conversation shapes (object-with-.messages / list)."""
    if isinstance(convo, list):
        return convo
    if isinstance(convo, dict):
        return list(convo.get("messages") or [])
    return []


def _is_unread_inbound(m) -> bool:
    direction = str(m.get("direction") or "").lower()
    acked = m.get("acknowledged", m.get("isAcknowledged", False))
    return direction == "in" and not acked


def scan_and_respond(client, run_async, *, state: dict,
                     cooldown_s: int = DEFAULT_COOLDOWN_S, now=None) -> list:
    """One pass: answer any pending challenges. Returns the replies made,
    each {'username', 'token'} — the caller logs/notifies them.

    ``state`` maps username -> last-reply epoch (caller-owned, survives
    across passes; an app restart resets it, which at one challenge an hour
    costs at most one extra polite reply)."""
    ts = time.time() if now is None else now
    replies = []
    convos = run_async(client.get_conversations()) or []
    for c in convos:
        username = str(c.get("username") or "")
        if not username:
            continue
        unread = c.get("hasUnAcknowledgedMessages") or (c.get("unAcknowledgedMessageCount") or 0) > 0
        if not unread:
            continue
        last = state.get(username)          # None = never replied — always eligible
        if last is not None and ts - last < cooldown_s:
            continue
        convo = run_async(client.get_conversation(username))
        token = None
        for m in _messages_of(convo):
            if not _is_unread_inbound(m):
                continue
            token = extract_token(m.get("message"))
            if token:
                break
        if not token:
            continue
        if not run_async(client.send_private_message(username, token)):
            logger.warning("autoprove: reply to %r failed (slskd rejected send)", username)
            continue
        state[username] = ts
        # Acknowledge so the badge doesn't nag about a challenge we answered.
        try:
            run_async(client.acknowledge_conversation(username))
        except Exception:
            logger.debug("autoprove: acknowledge failed for %r", username, exc_info=True)
        logger.info("autoprove: replied %r to %s's challenge", token, username)
        replies.append({"username": username, "token": token})
    return replies
