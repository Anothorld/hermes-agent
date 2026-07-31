"""WordPress draft publisher — bridges SEO Studio to the existing wordpress-mcp tool.

Reuses the operator's ``wordpress_mcp`` package (parser + client + publisher)
so SEO Studio stays in sync with the same publish pipeline the agent uses via
MCP. Credentials are read from the profile ``config.yaml``
(``mcp_servers.wordpress.env``) — the single source of truth — with environment
variables taking precedence when present.

Images are sideloaded into the WP Media Library by default so the first image
becomes the featured image. Set ``skip_image_upload=True`` to keep original
remote URLs instead (no featured image will be set).
"""

from __future__ import annotations

import os
import sys
import json
from pathlib import Path
from typing import Any

_WP_CONFIG_CACHE: dict[str, str] | None = None
_WP_IMPORT_READY = False


def _profile_config_path() -> Path:
    """Locate the povison-seo profile config.yaml.

    ``start.sh`` exports ``HERMES_HOME`` as the *profile* directory
    (``~/.hermes/profiles/povison-seo``), not the hermes root, so config.yaml
    sits directly under it. When HERMES_HOME is the hermes root (``~/.hermes``)
    the file is under ``profiles/{profile}/``. Try both, plus a third fallback.
    """
    hermes_home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    profile = os.environ.get("SEO_PROFILE", "povison-seo")
    candidates = [
        Path(hermes_home) / "config.yaml",                       # HERMES_HOME is the profile dir
        Path(hermes_home) / "profiles" / profile / "config.yaml",  # HERMES_HOME is the hermes root
        Path(hermes_home) / profile / "config.yaml",             # HERMES_HOME/profile
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]  # default; exists() checks will just fail gracefully


def _load_wp_env_from_config() -> dict[str, str]:
    """Read the ``mcp_servers.wordpress.env`` block from profile config.yaml.

    Returns an empty dict if the file or block is missing. Keeps the
    Application Password out of source — it only ever lives in config.yaml.
    """
    global _WP_CONFIG_CACHE
    if _WP_CONFIG_CACHE is not None:
        return _WP_CONFIG_CACHE
    env: dict[str, str] = {}
    try:
        import yaml  # type: ignore
    except ImportError:
        _WP_CONFIG_CACHE = env
        return env
    path = _profile_config_path()
    if not path.exists():
        _WP_CONFIG_CACHE = env
        return env
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        _WP_CONFIG_CACHE = env
        return env
    block = (doc.get("mcp_servers") or {}).get("wordpress") or {}
    raw_env = block.get("env") or {}
    if isinstance(raw_env, dict):
        for k, v in raw_env.items():
            env[str(k)] = str(v)
    _WP_CONFIG_CACHE = env
    return env


def _ensure_wp_env() -> dict[str, str]:
    """Merge config.yaml wordpress env into os.environ (env wins if already set).

    Returns the effective env dict so callers can inspect it without re-reading.
    """
    cfg = _load_wp_env_from_config()
    for k, v in cfg.items():
        if k == "PYTHONPATH":
            continue  # handled separately as the import path
        os.environ.setdefault(k, v)
    return cfg


def _ensure_import_path() -> str | None:
    """Add the wordpress_mcp src dir to sys.path so we can import it in-process.

    Resolution order: ``WORDPRESS_MCP_SRC`` env → config.yaml PYTHONPATH →
    default ``~/mcp-servers/wordpress-mcp/src``.
    """
    src = os.environ.get("WORDPRESS_MCP_SRC")
    if not src:
        cfg = _load_wp_env_from_config()
        src = cfg.get("PYTHONPATH")
    if not src:
        default = Path.home() / "mcp-servers" / "wordpress-mcp" / "src"
        if default.exists():
            src = str(default)
    if src and Path(src).exists() and src not in sys.path:
        sys.path.insert(0, src)
    return src


def _import_publisher() -> tuple[Any, Any, Any]:
    """Import (WPConfig, WPClient, publisher module) from wordpress_mcp.

    Raises a clear RuntimeError if the package or its deps are unavailable.
    """
    global _WP_IMPORT_READY
    _ensure_wp_env()
    _ensure_import_path()
    try:
        from wordpress_mcp import config as cfg_mod  # type: ignore
        from wordpress_mcp import publisher as pub_mod  # type: ignore
        from wordpress_mcp.client import WPClient  # type: ignore
    except ImportError as e:
        _WP_IMPORT_READY = False
        raise RuntimeError(
            f"wordpress_mcp not importable ({e}). Ensure the wordpress-mcp server "
            f"is installed (~/mcp-servers/wordpress-mcp) and its deps "
            f"(beautifulsoup4, requests) are in the Bridge venv."
        ) from e
    _WP_IMPORT_READY = True
    return cfg_mod.WPConfig, WPClient, pub_mod


def wp_config_snapshot() -> dict[str, Any]:
    """Return a non-secret snapshot of the effective WP config for health UI."""
    _ensure_wp_env()
    base = os.environ.get("WP_BASE", "")
    user = os.environ.get("WP_USER", "")
    has_pass = bool(os.environ.get("WP_APP_PASS"))
    return {
        "base_url": base,
        "username": user,
        "has_password": has_pass,
        "category_id": os.environ.get("WP_CATEGORY_ID", "62"),
        "tag_ids": os.environ.get("WP_TAG_IDS", ""),
        "seo_plugin": os.environ.get("SEO_PLUGIN", "rankmath"),
        "configured": bool(base and user and has_pass),
    }


def healthcheck() -> dict[str, Any]:
    """Verify REST API reachability + Application Password auth."""
    snap = wp_config_snapshot()
    if not snap["configured"]:
        return {**snap, "rest_api": "skipped", "auth": "skipped",
                "error": "WP_BASE/WP_USER/WP_APP_PASS not configured"}
    try:
        WPConfig, WPClient, _ = _import_publisher()
        cfg = WPConfig(
            base_url=os.environ.get("WP_BASE", "https://www.povison.com/blog"),
            username=os.environ.get("WP_USER", ""),
            app_password=os.environ.get("WP_APP_PASS", ""),
            category_id=int(os.environ.get("WP_CATEGORY_ID", "62")),
            tag_ids=_split_ids(os.environ.get("WP_TAG_IDS", "")),
            seo_plugin=os.environ.get("SEO_PLUGIN", "rankmath").lower(),
            delay_between_posts=float(os.environ.get("WP_DELAY_BETWEEN_POSTS", "3")),
            max_retries=int(os.environ.get("WP_MAX_RETRIES", "2")),
        )
        errors = cfg.validate()
        if errors:
            return {**snap, "rest_api": "skipped", "auth": "skipped", "errors": errors}
        client = WPClient(cfg)
        return {**snap, **client.healthcheck()}
    except Exception as e:
        return {**snap, "rest_api": "error", "auth": "error", "error": str(e)}


def _split_ids(value: str) -> list[int]:
    if not value.strip():
        return []
    return [int(x.strip()) for x in value.split(",") if x.strip()]


def _rank_math_rest_update_meta(config: Any, post_id: int, meta: dict[str, Any]) -> bool:
    """Write Rank Math post meta via Rank Math's own REST endpoint.

    The standard WP REST ``meta`` field silently drops ``rank_math_*`` keys
    because Rank Math does not register them with ``show_in_rest=true``, and
    XML-RPC ``custom_fields`` excludes registered meta — so neither the
    publisher's ``create_post(meta=...)`` nor ``update_post(meta=...)`` path
    persists SEO title/description/focus-keyword/schema. Rank Math exposes
    ``/wp-json/rankmath/v1/updateMeta`` (the same route the block editor calls
    on save) which accepts Basic Auth (application password) and persists the
    meta. Best-effort: returns False on failure so export never aborts.
    """
    if not post_id or not meta:
        return False
    try:
        import requests  # type: ignore
    except ImportError:
        return False
    url = config.base_url.rstrip("/") + "/wp-json/rankmath/v1/updateMeta"
    payload = {"postID": post_id, "objectID": post_id, "objectType": "post", "meta": meta}
    try:
        resp = requests.post(
            url,
            json=payload,
            auth=(config.username, config.app_password),
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _inject_rank_math_seo_meta(
    config: Any, result: dict[str, Any], focus_keyword: str | None = None
) -> bool:
    """Persist SEO title/description/focus-keyword via Rank Math REST.

    Reads the parsed SEO values the publisher already put on ``result["seo"]``
    and writes them through ``_rank_math_rest_update_meta`` (the standard
    REST ``meta`` write is silently dropped on this site). ``focus_keyword``
    (from ``articleState.meta.focus``) takes precedence over the parser's
    title-derived fallback.
    """
    if config.seo_plugin != "rankmath":
        return False
    if not result or not result.get("post_id"):
        return False
    seo = result.get("seo") or {}
    meta: dict[str, Any] = {}
    if seo.get("meta_title"):
        meta["rank_math_title"] = seo["meta_title"]
    if seo.get("meta_desc"):
        meta["rank_math_description"] = seo["meta_desc"]
    focus = (focus_keyword or seo.get("focus_kw") or "").strip()
    if focus:
        meta["rank_math_focus_keyword"] = focus
    if not meta:
        return False
    return _rank_math_rest_update_meta(config, result["post_id"], meta)


def publish_draft(
    *,
    html_content: str | None = None,
    html_path: str | None = None,
    category_id: int | None = None,
    tag_ids: list[int] | None = None,
    focus_keyword: str | None = None,
    skip_image_upload: bool = False,
    status: str = "draft",
) -> dict[str, Any]:
    """Create a WordPress draft from the SEO blog HTML.

    Delegates to ``wordpress_mcp.publisher.create_draft_from_html`` so the
    article body, SEO meta (Rank Math), FAQ schema, slug and featured image are
    extracted exactly as the agent's MCP tool does it.

    After the draft is created, two post-processing steps re-inject Rank Math
    meta through Rank Math's own REST endpoint (``/wp-json/rankmath/v1/
    updateMeta``): one for SEO title/description/focus-keyword and one for the
    FAQ schema. This is required because the standard WP REST ``meta`` field
    silently drops ``rank_math_*`` keys on this site (not registered with
    show_in_rest), so the publisher's own meta write never persists them.

    Args:
        html_content: Full blog template HTML (preferred). Parser extracts
            ``<article class="article-body">`` as post content and reads
            ``<title>``/``<meta description>``/``<canonical>``/FAQ JSON-LD from
            the head — so sending the full template yields the richest meta.
        html_path: Path to an HTML file (alternative to html_content).
        category_id: Override default WP category. None → config default.
        tag_ids: Override default tags. None → config default.
        focus_keyword: SEO focus keyword (from ``articleState.meta.focus``).
            Takes precedence over the parser's title-derived fallback.
        skip_image_upload: False (default) downloads + uploads each image to
            the WP Media Library so the first image becomes the featured image.
            True keeps original URLs (no featured image will be set).
        status: WP post status (draft by default).

    Returns:
        Publisher result dict with ``post_id``, ``edit_url``, ``preview_url``…
    """
    if not html_content and not html_path:
        raise ValueError("Provide html_content or html_path")
    WPConfig, WPClient, publisher = _import_publisher()
    cfg = WPConfig(
        base_url=os.environ.get("WP_BASE", "https://www.povison.com/blog"),
        username=os.environ.get("WP_USER", ""),
        app_password=os.environ.get("WP_APP_PASS", ""),
        category_id=int(os.environ.get("WP_CATEGORY_ID", "62")),
        tag_ids=_split_ids(os.environ.get("WP_TAG_IDS", "")),
        seo_plugin=os.environ.get("SEO_PLUGIN", "rankmath").lower(),
        delay_between_posts=float(os.environ.get("WP_DELAY_BETWEEN_POSTS", "3")),
        max_retries=int(os.environ.get("WP_MAX_RETRIES", "2")),
    )
    errors = cfg.validate()
    if errors:
        raise RuntimeError("WordPress config invalid: " + "; ".join(errors))
    client = WPClient(cfg)
    result = publisher.create_draft_from_html(
        client,
        cfg,
        html_path=html_path,
        html_content=html_content,
        status=status,
        category_id=category_id,
        tag_ids=tag_ids,
        focus_keyword=focus_keyword,
        skip_image_upload=skip_image_upload,
    )

    # Re-inject Rank Math SEO meta + FAQ schema via Rank Math's REST endpoint.
    # The publisher's standard REST `meta` write is silently dropped on this
    # site; these best-effort calls are what actually make the meta show up in
    # the Rank Math sidebar and on the rendered post.
    try:
        result["seo_meta_injected"] = _inject_rank_math_seo_meta(
            cfg, result, focus_keyword=focus_keyword
        )
    except Exception:
        result["seo_meta_injected"] = False
    _inject_rank_math_faq_schema(client, cfg, result, html_content, html_path)
    return result


def _inject_rank_math_faq_schema(
    client: Any,
    config: Any,
    result: dict[str, Any],
    html_content: str | None,
    html_path: str | None,
) -> None:
    """Write FAQ schema into Rank Math's ``rank_math_schema_FAQPage`` post meta.

    The publisher stores FAQ JSON-LD under ``faq_schema_json`` / a standard
    REST ``meta`` write, both of which Rank Math ignores (the REST ``meta``
    field silently drops ``rank_math_*`` keys on this site). Rank Math stores
    schema data keyed by type, e.g. ``rank_math_schema_FAQPage``. This helper
    re-parses the source HTML for the FAQ JSON-LD, builds a clean FAQPage
    object, and writes it through Rank Math's own REST endpoint
    (``/wp-json/rankmath/v1/updateMeta``).

    Best-effort: silently skips if there is no FAQ data or the update fails.
    """
    if not result or not result.get("post_id"):
        return
    if config.seo_plugin != "rankmath":
        return
    try:
        from wordpress_mcp.parser import parse_html_content, parse_html_file  # type: ignore
    except ImportError:
        return
    try:
        if html_path:
            parsed = parse_html_file(html_path)
        elif html_content:
            parsed = parse_html_content(html_content)
        else:
            return
    except Exception:
        return
    faq_data = parsed.get("faq_data")
    if not faq_data or not faq_data.get("mainEntity"):
        return
    # Rank Math stores the schema object directly (no @context; it adds that
    # on output). Strip @context/extra keys to match Rank Math's native shape.
    faq_schema = {"@type": "FAQPage", "mainEntity": faq_data.get("mainEntity") or []}
    try:
        ok = _rank_math_rest_update_meta(
            config,
            result["post_id"],
            {"rank_math_schema_FAQPage": json.dumps(faq_schema, ensure_ascii=False)},
        )
        result["faq_schema_injected"] = ok
    except Exception:
        result["faq_schema_injected"] = False
