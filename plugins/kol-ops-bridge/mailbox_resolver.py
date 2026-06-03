"""Campaign mailbox binding (sticky owner) + operator Gmail access control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from . import cal
from .gmail_client import GmailClient, GmailUnavailable
from .gmail_credentials import client_for_user

FACT_MAILBOX_USER_ID = "offer.gmail_mailbox_user_id"
FACT_MAILBOX_EMAIL = "offer.gmail_mailbox_email"
FACT_MAILBOX_BOUND_AT = "offer.gmail_mailbox_bound_at"
FACT_MAILBOX_TAKEOVER_AT = "offer.gmail_mailbox_takeover_at"
FACT_GMAIL_THREADS_STALE = "offer.gmail_threads_stale_after_takeover"


class MailboxError(Exception):
    """Base mailbox resolution error with HTTP mapping hints."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class GmailNotConnectedError(MailboxError):
    def __init__(self, user_id: int) -> None:
        super().__init__(
            "gmail_not_connected",
            f"operator user_id={user_id} has not connected Gmail in KOL Ops Console",
            status_code=409,
        )


class MailboxNotOwnerError(MailboxError):
    def __init__(self, *, bound_user_id: int, bound_email: str, operator_user_id: int) -> None:
        super().__init__(
            "mailbox_not_owner",
            (
                f"this campaign mailbox is owned by user_id={bound_user_id} "
                f"({bound_email}); current operator is {operator_user_id}"
            ),
            status_code=409,
        )


class MailboxAccessDeniedError(MailboxError):
    def __init__(self, *, bound_email: str) -> None:
        self.bound_email = bound_email
        super().__init__(
            "mailbox_access_denied",
            f"communication history is only available to the mailbox owner ({bound_email})",
            status_code=403,
        )


class OperatorRequiredError(MailboxError):
    def __init__(self) -> None:
        super().__init__(
            "operator_required",
            "X-KOC-Operator-User-Id is required for Gmail communication history",
            status_code=401,
        )


class TakeoverNotAllowedError(MailboxError):
    def __init__(self, *, bound_email: str, bound_user_id: int) -> None:
        self.bound_email = bound_email
        self.bound_user_id = bound_user_id
        super().__init__(
            "takeover_not_allowed",
            (
                f"campaign mailbox is owned by user_id={bound_user_id} ({bound_email}); "
                "only console owners may reassign to another operator"
            ),
            status_code=403,
        )


@dataclass(frozen=True)
class MailboxBinding:
    user_id: int
    email: str
    bound_at: Optional[str] = None


@dataclass(frozen=True)
class ResolvedMailbox:
    binding: Optional[MailboxBinding]
    client: GmailClient
    operator_user_id: Optional[int]


def read_binding(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
) -> Optional[MailboxBinding]:
    facts = cal.latest_facts_for(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
    )
    raw_uid = facts.get(FACT_MAILBOX_USER_ID)
    raw_email = facts.get(FACT_MAILBOX_EMAIL)
    if raw_uid is None or raw_email is None:
        return None
    try:
        uid = int(raw_uid)
    except (TypeError, ValueError):
        return None
    email = str(raw_email).strip().lower()
    if not email:
        return None
    bound_at = facts.get(FACT_MAILBOX_BOUND_AT)
    return MailboxBinding(
        user_id=uid,
        email=email,
        bound_at=str(bound_at) if bound_at else None,
    )


def bind_mailbox(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    operator_user_id: int,
    operator_email: str,
    source: str,
) -> MailboxBinding:
    """Sticky-bind campaign mailbox to the operator (no-op if already bound)."""
    existing = read_binding(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
    )
    if existing is not None:
        if existing.user_id != operator_user_id:
            raise MailboxNotOwnerError(
                bound_user_id=existing.user_id,
                bound_email=existing.email,
                operator_user_id=operator_user_id,
            )
        return existing
    client = client_for_user(operator_user_id)
    if not client.is_available():
        raise GmailNotConnectedError(operator_user_id)
    profile_email = client.get_profile_email() or operator_email.strip().lower()
    from datetime import datetime, timezone

    bound_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cal.write_facts(
        identity_id=identity_id,
        campaign_id=campaign_id,
        namespace="offer",
        facts={
            FACT_MAILBOX_USER_ID: operator_user_id,
            FACT_MAILBOX_EMAIL: profile_email,
            FACT_MAILBOX_BOUND_AT: bound_at,
        },
        source=source,
        env=env,
    )
    return MailboxBinding(
        user_id=operator_user_id,
        email=profile_email,
        bound_at=bound_at,
    )


def assert_takeover_allowed(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    new_operator_user_id: int,
    requester_role: str,
) -> None:
    """Only owners may steal a bound mailbox; unbound campaigns are first-claim."""
    if str(requester_role).lower() == "owner":
        return
    existing = read_binding(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
    )
    if existing is None:
        return
    if existing.user_id == new_operator_user_id:
        return
    raise TakeoverNotAllowedError(
        bound_email=existing.email,
        bound_user_id=existing.user_id,
    )


def resolve_for_inbound_gmail(
    *,
    identity_id: int,
    campaign_id: Optional[str],
    env: str,
    detected_mailbox_user_id: Optional[int] = None,
) -> GmailClient:
    """Gmail client for inbound label mutations (poller / reply-dispatcher).

    Prefers the mailbox that received the message, then the campaign binding.
    """
    uid: Optional[int] = None
    if detected_mailbox_user_id is not None and detected_mailbox_user_id > 0:
        uid = int(detected_mailbox_user_id)
    elif campaign_id:
        binding = read_binding(
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
        )
        if binding is not None:
            uid = binding.user_id
    client = client_for_user(uid)
    if not client.is_available():
        raise GmailNotConnectedError(uid if uid is not None else 0)
    return client


def takeover_mailbox(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    new_operator_user_id: int,
    operator_email: str,
    source: str,
    requester_role: str = "operator",
) -> MailboxBinding:
    assert_takeover_allowed(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        new_operator_user_id=new_operator_user_id,
        requester_role=requester_role,
    )
    client = client_for_user(new_operator_user_id)
    if not client.is_available():
        raise GmailNotConnectedError(new_operator_user_id)
    profile_email = client.get_profile_email() or operator_email.strip().lower()
    from datetime import datetime, timezone

    bound_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prior = read_binding(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
    )
    cal.write_facts(
        identity_id=identity_id,
        campaign_id=campaign_id,
        namespace="offer",
        facts={
            FACT_MAILBOX_USER_ID: new_operator_user_id,
            FACT_MAILBOX_EMAIL: profile_email,
            FACT_MAILBOX_BOUND_AT: bound_at,
            FACT_MAILBOX_TAKEOVER_AT: bound_at,
            FACT_GMAIL_THREADS_STALE: True,
        },
        source=source,
        env=env,
    )
    cal.write_event(
        identity_id=identity_id,
        campaign_id=campaign_id,
        event_type="mailbox.takeover",
        goal="outreach",
        lane="commerce",
        actor=source,
        payload={
            "new_user_id": new_operator_user_id,
            "new_email": profile_email,
            "previous_user_id": prior.user_id if prior else None,
            "previous_email": prior.email if prior else None,
            "threads_stale": True,
            "operator_note": (
                "Gmail thread_ids from the previous mailbox may not load. "
                "Reconcile sent mail or resend from the new mailbox if needed."
            ),
        },
        env=env,
    )
    return MailboxBinding(
        user_id=new_operator_user_id,
        email=profile_email,
        bound_at=bound_at,
    )


def resolve_for_write(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    operator_user_id: Optional[int],
    operator_email: str = "",
    bind_if_unbound: bool = True,
    source: str = "bridge:mailbox",
) -> ResolvedMailbox:
    if operator_user_id is None:
        client = client_for_user(None)
        return ResolvedMailbox(binding=None, client=client, operator_user_id=None)

    binding = read_binding(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
    )
    if binding is None and bind_if_unbound:
        binding = bind_mailbox(
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
            operator_user_id=operator_user_id,
            operator_email=operator_email,
            source=source,
        )
    elif binding is not None and binding.user_id != operator_user_id:
        raise MailboxNotOwnerError(
            bound_user_id=binding.user_id,
            bound_email=binding.email,
            operator_user_id=operator_user_id,
        )
    elif binding is None:
        client = client_for_user(operator_user_id)
        if not client.is_available():
            raise GmailNotConnectedError(operator_user_id)
        return ResolvedMailbox(binding=None, client=client, operator_user_id=operator_user_id)

    client = client_for_user(binding.user_id if binding else operator_user_id)
    if not client.is_available():
        raise GmailNotConnectedError(binding.user_id if binding else operator_user_id)
    return ResolvedMailbox(
        binding=binding,
        client=client,
        operator_user_id=operator_user_id,
    )


def resolve_for_read(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    operator_user_id: Optional[int],
) -> ResolvedMailbox:
    if operator_user_id is None or operator_user_id < 1:
        raise OperatorRequiredError()
    binding = read_binding(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
    )
    if binding is None:
        client = client_for_user(operator_user_id)
        if not client.is_available():
            raise GmailNotConnectedError(operator_user_id)
        return ResolvedMailbox(binding=None, client=client, operator_user_id=operator_user_id)
    if operator_user_id is None or operator_user_id != binding.user_id:
        raise MailboxAccessDeniedError(bound_email=binding.email)
    client = client_for_user(binding.user_id)
    if not client.is_available():
        raise GmailNotConnectedError(binding.user_id)
    return ResolvedMailbox(binding=binding, client=client, operator_user_id=operator_user_id)


def mailbox_error_to_http(exc: MailboxError) -> dict[str, Any]:
    return {
        "code": exc.code,
        "message": exc.message,
        "bound_mailbox_email": getattr(exc, "bound_email", None),
    }
