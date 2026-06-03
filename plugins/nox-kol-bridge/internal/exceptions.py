"""Shared exceptions for nox-kol-bridge."""

from internal.nox_auth import NoxAuthError  # noqa: F401 — re-exported

__all__ = ["NoxAuthError", "NoxCampaignGateError"]


class NoxCampaignGateError(ValueError):
    """Campaign config or console dispatch blocks this Nox operation."""
