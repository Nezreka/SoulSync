"""a measured, usable peer is better evidence than a peer nobody measured.

anything under the healthy mark was the bottom class: a peer measured at
300-499 KB/s with a free slot ranked below an unmeasured peer with no free
slot and three hundred in its queue.
"""

from types import SimpleNamespace as NS

from core.downloads.peer_observation import HEALTHY_PEER_BPS, peer_availability_key


def _peer(slots=0, queue=0):
    return NS(free_upload_slots=slots, queue_length=queue, upload_speed=0)


def test_a_decently_measured_peer_outranks_an_unmeasured_one():
    decent = peer_availability_key(_peer(slots=1), 350_000)
    unknown = peer_availability_key(_peer(slots=0, queue=300), None)
    assert decent > unknown


def test_the_order_of_the_four_classes():
    healthy = peer_availability_key(_peer(), HEALTHY_PEER_BPS)
    decent = peer_availability_key(_peer(), HEALTHY_PEER_BPS / 2)
    unknown = peer_availability_key(_peer(slots=5), None)
    crawler = peer_availability_key(_peer(slots=5), 20_000)
    assert healthy > decent > unknown > crawler
