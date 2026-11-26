"""what counts as slskd accepting an enqueue"""

from core.slskd_transfer import acknowledged_transfer_id as ack


def test_current_slskd_names_the_transfer():
    body = {"enqueued": [{"id": "5f2", "username": "u", "filename": "a.flac"}], "failed": []}
    assert ack(body, "u", "a.flac") == "5f2"


def test_older_slskd_says_nothing_and_that_is_accepted():
    assert ack({}, "u", "a.flac") == "a.flac"


def test_a_failure_or_no_answer_is_not_accepted():
    assert ack(None, "u", "a.flac") is None
    assert ack({"enqueued": [], "failed": ["Skipped: Already in progress"]}, "u", "a.flac") is None
