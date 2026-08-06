"""SSRF guard for server-side link fetching (link_pull). Blocks non-public
hosts so a pasted URL can't reach localhost / cloud-metadata / internal IPs.

Offline: literal-IP hosts resolve via getaddrinfo locally (no network), and
non-http schemes are rejected before any resolution."""
from __future__ import annotations

import pytest

from app.link_pull import LinkPullError, _assert_public_http_url


@pytest.mark.parametrize("url", [
    "ftp://example.com/x",                         # non-http scheme
    "file:///etc/passwd",                          # non-http scheme
    "http://",                                     # no host
    "http://127.0.0.1/x",                          # loopback
    "http://[::1]/x",                              # loopback v6
    "http://169.254.169.254/latest/meta-data",     # link-local (cloud metadata)
    "http://10.1.2.3/x",                           # private
    "http://192.168.0.1/x",                        # private
    "http://172.16.5.5/x",                         # private
    "http://0.0.0.0/x",                            # unspecified
])
def test_blocks_unsafe(url):
    with pytest.raises(LinkPullError):
        _assert_public_http_url(url)


@pytest.mark.parametrize("url", [
    "http://8.8.8.8/file.pdf",                      # public literal IP
    "https://1.1.1.1/doc.zip",                      # public literal IP
])
def test_allows_public(url):
    _assert_public_http_url(url)  # must not raise
