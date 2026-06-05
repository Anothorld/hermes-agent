"""Build ``noxinfluencer creator`` CLI argument lists (selector mutual exclusion)."""

from __future__ import annotations

from typing import Optional


def creator_id_unsafe_for_cli(creator_id: str) -> bool:
    """True when Commander would mis-parse the ID as a flag."""
    return str(creator_id).strip().startswith("-")


def prefer_cli_selector_over_dash_id(
    creator_id: Optional[str],
    url: Optional[str],
    platform: Optional[str],
    channel_id: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Use ``--url`` / ``--platform`` when ID starts with ``-`` and selectors exist."""
    if not creator_id or not creator_id_unsafe_for_cli(creator_id):
        return creator_id, url, platform, channel_id
    if url:
        return None, url, platform, channel_id
    if platform and channel_id:
        return None, url, platform, channel_id
    return creator_id, url, platform, channel_id


def needs_direct_creator_http(
    creator_id: Optional[str],
    url: Optional[str],
    platform: Optional[str],
    channel_id: Optional[str],
) -> bool:
    """True when the ID cannot be passed to ``noxinfluencer`` and no URL fallback."""
    cid, u, p, ch = prefer_cli_selector_over_dash_id(
        creator_id, url, platform, channel_id
    )
    return bool(cid) and creator_id_unsafe_for_cli(cid) and not u and not (p and ch)


def build_creator_read_args(
    dimension: str,
    *,
    creator_id: Optional[str],
    url: Optional[str],
    platform: Optional[str],
    channel_id: Optional[str],
    detail: bool = False,
) -> list[str]:
    """Return argv fragment after ``creator`` (dimension + selector + flags)."""
    creator_id, url, platform, channel_id = prefer_cli_selector_over_dash_id(
        creator_id, url, platform, channel_id
    )
    args = [dimension]
    if creator_id:
        args.append(str(creator_id).strip())
    elif url:
        args.extend(["--url", url])
    elif platform and channel_id:
        args.extend(["--platform", platform, "--channel-id", channel_id])
    if detail:
        args.append("--detail")
    return args
