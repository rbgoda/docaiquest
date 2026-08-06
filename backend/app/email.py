"""M48 · transactional email via Resend (signup verification).

Single dependency-light sender using Resend's HTTP API. When
DOCAIQ_RESEND_API_KEY is empty, we LOG the message (and any link) instead of
sending — so the verification flow is fully testable locally with no provider.

Never raises into the request path: a send failure is logged and swallowed
(the caller decides whether the email is critical; for verification it isn't —
the user can resend).
"""
from __future__ import annotations

import logging

import httpx

from app.config import get_settings

log = logging.getLogger("docaiq.email")

_RESEND_URL = "https://api.resend.com/emails"


def send_email(*, to: str, subject: str, html: str, text: str | None = None) -> bool:
    """Send one email. Returns True if handed off to Resend (or logged in dev).
    Best-effort: returns False on failure, never raises."""
    s = get_settings()
    if not s.resend_api_key:
        log.info("email (dev, not sent) → %s · %s\n%s", to, subject, text or html)
        return True
    try:
        payload = {"from": s.email_from, "to": [to], "subject": subject, "html": html}
        if text:
            payload["text"] = text
        r = httpx.post(_RESEND_URL, json=payload, timeout=15,
                       headers={"Authorization": f"Bearer {s.resend_api_key}",
                                "Content-Type": "application/json"})
        if r.status_code >= 300:
            log.warning("email send failed (%s) → %s: %s", r.status_code, to, r.text[:300])
            return False
        log.info("email sent → %s · %s", to, subject)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("email send error → %s: %s", to, e)
        return False


def send_verification_email(*, to: str, name: str | None, verify_url: str) -> bool:
    greeting = f"Hi {name}," if name else "Hi,"
    subject = "Verify your email · DocAIQ"
    html = f"""\
<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:440px;margin:0 auto;color:#1a1a1a">
  <h2 style="font-weight:600">Confirm your email</h2>
  <p>{greeting}</p>
  <p>Thanks for signing up for <b>DocAIQ Documents</b>. Please confirm your email to finish setting up your account.</p>
  <p style="margin:24px 0">
    <a href="{verify_url}" style="background:#C8A04C;color:#1a1408;text-decoration:none;
       padding:11px 22px;border-radius:999px;font-weight:600;display:inline-block">Verify email</a>
  </p>
  <p style="color:#666;font-size:13px">Or paste this link into your browser:<br>
    <a href="{verify_url}" style="color:#9a7b2e;word-break:break-all">{verify_url}</a></p>
  <p style="color:#999;font-size:12px;margin-top:28px">If you didn't create a DocAIQ account, you can ignore this email.</p>
</div>"""
    text = f"{greeting}\n\nConfirm your DocAIQ email by opening this link:\n{verify_url}\n\nIf you didn't sign up, ignore this email."
    return send_email(to=to, subject=subject, html=html, text=text)
