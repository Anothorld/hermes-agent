"""IG Reels extraction — navigate to reels tab and extract structured reel list.

Extracts up to max_reels items with:
- reel_url
- views, likes, comments
- thumbnail_url (from grid img[src] or poster)
- posted_at / posted_within_hours (if available)

Then runs reels-level qualification gates via ``qualify_evaluator``,
merging with any existing profile-level gates.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Hyphenated directory can't use package imports
_INTERNAL_DIR = str(Path(__file__).resolve().parent)
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

from content_eval import build_content_eval_plan  # noqa: E402
from errors import DomChangedError  # noqa: E402
from qualify_evaluator import evaluate_reels_gates  # noqa: E402
from risk_detector import raise_on_risk  # noqa: E402


# JS to extract reels data from IG profile /reels/ page
_REELS_JS = """
(() => {
  const reels = [];
  // IG reels grid items — multiple selector paths for resilience
  const selectors = [
    'a[href*="/reel/"]',
    'article a[href*="/reel/"]',
    'div[role="tab"] a[href*="/reel/"]',
    'a[href*="/p/"][role="tab"]',
  ];
  let reelLinks = [];
  for (const sel of selectors) {
    const found = document.querySelectorAll(sel);
    if (found.length > 0) {
      reelLinks = Array.from(found);
      break;
    }
  }

  for (const link of reelLinks.slice(0, 20)) {
    const href = link.href || '';
    const reelMatch = href.match(/(?:reel|p)\\/([A-Za-z0-9_-]+)/);
    if (!reelMatch) continue;
    const reelId = reelMatch[1];

    // Extract thumbnail — IG reels grid uses CSS background-image on <div>,
    // not <img> tags. Check img first (older layout), then background-image
    // (current bloks layout), then video poster.
    let thumbnailUrl = '';
    const img = link.querySelector('img') || link.closest('article')?.querySelector('img');
    if (img) {
      thumbnailUrl = img.src || img.dataset?.src || '';
    }
    if (!thumbnailUrl) {
      const bgEl = link.querySelector('[style*="background-image"]');
      if (bgEl) {
        const style = bgEl.getAttribute('style') || '';
        const bm = style.match(/background-image:\\s*url\\(["']?([^"')]+)/);
        if (bm) thumbnailUrl = bm[1];
      }
    }
    if (!thumbnailUrl) {
      const video = link.querySelector('video');
      if (video && video.poster) thumbnailUrl = video.poster;
    }

    // Views/likes/comments may be in overlay text or aria-label
    const ariaLabel = link.getAttribute('aria-label') || '';
    const linkText = link.textContent || '';
    const infoText = ariaLabel || linkText;

    // Parse view count. Locale-aware across en/zh/es/fr/ja/ko. Two layouts:
    //   (a) number BEFORE play-word: "1.2M plays", "4.5万 次播放"
    //   (b) play-word BEFORE number: "观看量图标5630万" (zh pinned-reel grid text),
    //       "Plays 1.2M". The debug Chrome renders IG in the operator locale, so
    //       an English-only "plays|views" regex silently returned views=0 on
    //       non-English locales and tripped the avg_views_below_30k hard gate.
    let views = 0;
    const parseCount = (raw) => {
      const num = parseFloat(raw.replace(/,/g, ''));
      const suffix = raw.match(/[KkMmBb万亿]/i)?.[0]?.toLowerCase() || '';
      const mult = {k:1000, m:1e6, b:1e9, '万':1e4, '亿':1e8}[suffix] || 1;
      return Math.round(num * mult);
    };
    // (a) number before word
    let m = infoText.match(/([\\d.,KkMmBb万亿]+)\\s*(?:plays|views|次播放|播放量?|次观看|观看量(?:图标)?|观看|reproducciones|vues|視聴回数|視聴|재생\\s*횟수|조회수)/i);
    if (m) { views = parseCount(m[1]); }
    else {
      // (b) word before number
      m = infoText.match(/(?:plays|views|次播放|播放量?|次观看|观看量(?:图标)?|观看|reproducciones|vues|視聴回数|視聴|재생\\s*횟수|조회수)\\s*[：:]?\\s*([\\d.,KkMmBb万亿]+)/i);
      if (m) { views = parseCount(m[1]); }
      else {
        // Structural fallback: aria-label is just a bare count (e.g. "4.5万").
        const bare = infoText.match(/^\\s*([\\d.,]+\\s*[KkMmBb万亿]?)\\s*$/);
        if (bare) views = parseCount(bare[1]);
      }
    }

    reels.push({
      reel_id: reelId,
      url: `https://www.instagram.com/reel/${reelId}/`,
      thumbnail_url: thumbnailUrl,
      views: views,
      likes: 0,  // Not available from grid — need reel page
      comments: 0,  // Not available from grid
      posted_at: null,
      posted_within_hours: null,
    });
  }

  return { reels, count: reels.length };
})()
"""


def fetch_reels(
    runner,
    handle: str,
    max_reels: int = 10,
    *,
    include_content_eval: bool = True,
    eval_mode: str | None = None,
) -> dict:
    """Navigate to IG profile reels tab and extract reels + qualification.

    Args:
        runner: A ``CdpRunner`` instance.
        handle: IG handle (with or without @).
        max_reels: Max reels to extract (default 10 for content screening).
        include_content_eval: When True, attach a ``content_eval`` block with
            the first ``max_reels`` covers and (when video mode is ON) a random
            sample of 3 reels for ``rpa_download_ig_content``.
        eval_mode: Optional ``"cover"`` / ``"video"`` override for the plan.

    Returns:
        Dict with reels ``data`` and merged ``qualification``.

    Raises:
        DomChangedError: If JS extraction returns empty data.
        CheckpointError: If risk page detected.
        SessionExpiredError: If login wall detected.
    """
    import pacing

    clean_handle = handle.lstrip("@")
    url = f"https://www.instagram.com/{clean_handle}/reels/"

    pacing.jitter_delay("reel")
    pacing.mark_reel_load(runner.task_id)

    resp = runner.navigate(url)
    if not resp.get("success"):
        raise DomChangedError(f"navigate to reels failed: {resp.get('error')}")

    data = resp.get("data", {})
    snapshot_text = data.get("snapshot", "")
    raise_on_risk(snapshot_text)

    # The /reels/ SPA grid hydrates AFTER navigate's readyState check passes —
    # the first eval otherwise returns 0 reels and trips a false
    # static_only_account discard. Settle ~1s before extracting.
    import random as _random
    import time as _time
    _time.sleep(_random.uniform(0.9, 1.3))

    # Extract structured reels data via JS
    js_result = runner.eval(_REELS_JS)
    if not js_result or not isinstance(js_result, dict):
        raise DomChangedError("JS reels extraction returned empty or non-dict result")

    reels = js_result.get("reels", [])[:max_reels]

    if not reels:
        # Try scrolling once to trigger lazy-load of further reels
        _time.sleep(_random.uniform(0.4, 0.8))
        runner.scroll(1)
        _time.sleep(_random.uniform(0.4, 0.8))
        js_result = runner.eval(_REELS_JS)
        if js_result and isinstance(js_result, dict):
            reels = js_result.get("reels", [])[:max_reels]

    # Run reels-level qualification gates (profile gates may be merged by caller)
    # For now, just evaluate reels gates without profile gates
    qual = evaluate_reels_gates(reels)

    data: dict = {
        "handle": clean_handle,
        "reels": reels,
        "count": len(reels),
    }
    if include_content_eval:
        # cover_count comes from EVAL_COVER_COUNT (vision/cost cap), not max_reels.
        # max_reels still sizes the grid extract for activity gates + comments.
        data["content_eval"] = build_content_eval_plan(
            reels,
            eval_mode=eval_mode,
            handle=clean_handle,
        )

    return {
        "data": data,
        "qualification": qual,
    }
