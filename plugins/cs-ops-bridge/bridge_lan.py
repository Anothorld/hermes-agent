"""LAN-facing address helpers for expert upload links."""

from __future__ import annotations

import os
import socket


def local_lan_ipv4() -> str:
    """Best-effort primary IPv4 on the local network (not loopback)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if not addr.startswith("127."):
                return addr
    except OSError:
        pass
    return "127.0.0.1"


def bridge_listen_port() -> int:
    for key in ("CSCS_BRIDGE_PORT", "CS_OPS_BRIDGE_PORT", "PORT"):
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        try:
            return int(raw)
        except ValueError:
            continue
    return 8081


def default_vault_public_base() -> str:
    """Upload link base for phones/laptops on the same LAN as the bridge host."""
    ip = local_lan_ipv4()
    port = bridge_listen_port()
    return f"http://{ip}:{port}/api/plugins/cs-ops-bridge"
