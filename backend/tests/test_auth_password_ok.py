"""password_ok — constant-time login verify that closes the user-enumeration
timing oracle (always runs argon2, even on a miss)."""
from app.auth import hash_password, password_ok


def test_password_ok_correct():
    h = hash_password("s3cret-pw")
    assert password_ok("s3cret-pw", h) is True
    assert password_ok("wrong-pw", h) is False


def test_password_ok_miss_returns_false():
    # No stored hash (non-existent / OAuth-only account) → False, but still runs a
    # dummy argon2 verify internally so timing doesn't reveal the account is missing.
    assert password_ok("anything", None) is False
    assert password_ok("anything", "") is False
