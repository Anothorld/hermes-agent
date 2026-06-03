"""Build ``noxinfluencer creator`` CLI argument lists (selector mutual exclusion)."""

from __future__ import annotations

from typing import Optional


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
    args = [dimension]
    if creator_id:
        args.append(creator_id)
    elif url:
        args.extend(["--url", url])
    elif platform and channel_id:
        args.extend(["--platform", platform, "--channel-id", channel_id])
    if detail:
        args.append("--detail")
    return args
