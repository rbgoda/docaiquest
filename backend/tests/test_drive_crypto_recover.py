"""decrypt_blob_recover must only try the current owner key + EXPLICIT prior
pks — never brute-force the global pk space (security fix)."""
from __future__ import annotations

import pytest

from app import drive_crypto as dc


def _enc(owner, data=b"secret-bytes"):
    return dc.encrypt_blob(owner, data, enabled=True)


def test_roundtrip_current_owner():
    blob = _enc(7)
    assert dc.decrypt_blob_recover(7, blob) == b"secret-bytes"


def test_recovers_with_explicit_prior_pk():
    blob = _enc(5)                       # encrypted under pk 5
    # current owner is 9 now; 5 is a known prior pk → recovers
    assert dc.decrypt_blob_recover(9, blob, candidate_ids=[3, 5]) == b"secret-bytes"


def test_no_bruteforce_without_candidates():
    blob = _enc(5)
    # wrong owner, no candidates → must NOT find it (no global brute-force)
    with pytest.raises(Exception):
        dc.decrypt_blob_recover(9, blob)


def test_wrong_candidates_raise():
    blob = _enc(5)
    with pytest.raises(Exception):
        dc.decrypt_blob_recover(9, blob, candidate_ids=[2, 3, 4])


def test_plaintext_passthrough():
    assert dc.decrypt_blob_recover(1, b"not encrypted") == b"not encrypted"


def test_candidate_cap(monkeypatch):
    # a huge candidate list is capped — only the first _MAX_RECOVER_CANDIDATES tried
    monkeypatch.setattr(dc, "_MAX_RECOVER_CANDIDATES", 3)
    blob = _enc(50)
    # 50 is beyond the first 3 candidates → not tried → raises
    with pytest.raises(Exception):
        dc.decrypt_blob_recover(9, blob, candidate_ids=[1, 2, 3, 50])
