"""Customer email lookup for Feishu escalation (excerpt comes from the agent)."""

from __future__ import annotations

from .email_channel import fetch_email_session_row


def resolve_customer_email(*, quickcep_session_id: str, env: str = "LIVE") -> str:
    """Best-effort customer email from CAL or QuickCEP session list."""
    from . import cal

    sess = cal.get_session(quickcep_session_id=quickcep_session_id, env=env)
    if sess and sess.get("customer_email"):
        return str(sess["customer_email"]).strip()

    row = fetch_email_session_row(quickcep_session_id)
    if row:
        vi = row.get("visitorInfo") if isinstance(row.get("visitorInfo"), dict) else {}
        email = row.get("email") or vi.get("email")
        if email:
            return str(email).strip()
    return ""
