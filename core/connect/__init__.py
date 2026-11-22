"""listen anywhere: the server's own identity, paired devices, invite codes
and the address list (PAIRING_SPEC.md).

nothing here talks to the connect service yet. the identity and the sealing
are copies of MetaSync's (identity.py, meta/sealing.py), not imports: the
pinned vectors in tests/test_connect_conformance.py are the contract.
"""
