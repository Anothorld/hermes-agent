"""Reel comments extraction — navigate to a Reel page and extract comments.

Two modes:
- ``evaluation``: Return comments[] + caption + hashtags + thumbnail_url
  (for KOL content screening — style/audience understanding).
- ``discovery``: Return commenter handles with follower hints
  (for lateral discovery — finding new candidates in comment sections).

First viewport only — does not scroll or expand replies (per skill L320-322).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Hyphenated directory can't use package imports
_INTERNAL_DIR = str(Path(__file__).resolve().parent)
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

from errors import DomChangedError  # noqa: E402
from risk_detector import raise_on_risk  # noqa: E402


_COMMENTS_JS = """
(() => {
  const result = {
    caption: '',
    hashtags: [],
    comments: [],
    commenters: [],
    thumbnail_url: '',
    total_visible: 0,
    reel_likes: 0,
    reel_comments_count: 0,
  };

  // Thumbnail from og:image (reliable across locales).
  const ogImage = document.querySelector('meta[property="og:image"]');
  if (ogImage) result.thumbnail_url = ogImage.getAttribute('content') || '';
  const videoEl = document.querySelector('video');
  if (!result.thumbnail_url && videoEl && videoEl.poster) {
    result.thumbnail_url = videoEl.poster;
  }

  // Caption + reel-level likes/comments from og:description — format (locale-varied):
  //   "<likes> likes, <N> comments - <author>，<date> : \\"<caption>\\""
  //   "<likes> 次赞，<N> 条评论 - <author>，<date> : \\"<caption>\\""
  // The caption is the text inside the trailing quotes. The reel page uses IG's
  // "bloks" component system (no <article>/<ul>/<li>), so caption selectors are
  // unreliable; og:description is locale-stable and always populated. We also
  // parse the leading "<N> likes, <N> comments" to get reel-level engagement
  // (the /reels/ grid only exposes views, not likes/comments — without this,
  // the ER gate sees likes=0/comments=0 for every reel and hard-discards every
  // candidate with a false reel_er_below_3pct).
  const extractQuoted = (s) => {
    if (!s) return '';
    const m = s.match(/["“”"]\\s*([^“”""]{5,})\\s*["”""]\\s*$/);
    return m ? m[1].slice(0, 1000) : '';
  };
  const parseCount = (raw) => {
    if (!raw) return 0;
    const num = parseFloat(raw.replace(/,/g, ''));
    if (isNaN(num)) return 0;
    const suffix = (raw.match(/[KkMmBb万亿]/i) || [])[0] || '';
    const mult = {k:1e3, K:1e3, m:1e6, M:1e6, b:1e9, B:1e9, '万':1e4, '亿':1e8}[suffix] || 1;
    return Math.round(num * mult);
  };
  const ogDesc = document.querySelector('meta[property="og:description"]');
  const ogDescRaw = ogDesc ? (ogDesc.getAttribute('content') || '') : '';
  if (ogDescRaw) {
    // Engagement prefix is everything before the first " - " separator.
    const dashIdx = ogDescRaw.indexOf(' - ');
    const prefix = dashIdx > 0 ? ogDescRaw.slice(0, dashIdx) : '';
    if (prefix) {
      // "1,234 likes, 56 comments" / "1,234 次赞，56 条评论" / "12.3K likes · 456 comments"
      const lm = prefix.match(/([\\d.,]+\\s*[KkMmBb万亿]?)\\s*(?:likes?|次赞|赞|j'aime|gefällt|me gusta)/i);
      if (lm) result.reel_likes = parseCount(lm[1]);
      const cm = prefix.match(/([\\d.,]+\\s*[KkMmBb万亿]?)\\s*(?:comments?|条评论|评论|commentaires?|comentarios?)/i);
      if (cm) result.reel_comments_count = parseCount(cm[1]);
    }
    result.caption = extractQuoted(ogDescRaw);
  }
  if (!result.caption) {
    const ogTitle = document.querySelector('meta[property="og:title"]');
    if (ogTitle) result.caption = extractQuoted(ogTitle.getAttribute('content') || '');
  }
  if (result.caption) {
    const hm = result.caption.match(/#[A-Za-z0-9_]+/g);
    if (hm) result.hashtags = hm;
  }

  // Author handle from the URL path (/<handle>/reel/<id>/) — used to exclude the
  // author from the commenter list. Falls back to the first profile link.
  const pathAuthor = (location.pathname.match(/^\\/([A-Za-z0-9._]+)\\/reel\\//) || [])[1] || '';
  const bodyText = ((document.body && document.body.innerText) || '').replace(/\\s+/g, ' ').trim();

  // Commenters = profile links a[href="/<handle>/"] whose link text equals the
  // handle (excludes nav like "主页"/Home, and locale "已验证"/verified suffix).
  const handleHrefRe = /^\\/[A-Za-z0-9._]+\\/$/;
  const seen = new Set();
  let firstHandle = '';
  const rawCommenters = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || '';
    if (!handleHrefRe.test(href)) continue;
    const text = (a.textContent || '').replace(/\\s+/g, ' ').trim();
    if (!text) continue;
    const handle = text.replace(/已验证|verified/gi, '').trim().replace(/^@/, '');
    if (!handle || href.replace(/\\//g, '') !== handle) continue;
    if (seen.has(handle)) continue;
    seen.add(handle);
    if (!firstHandle) firstHandle = handle;
    rawCommenters.push({ handle, href: a.href, is_verified: /已验证|verified/i.test(text) });
    if (rawCommenters.length >= 50) break;
  }
  const author = pathAuthor || firstHandle;
  // Commenters excluding the author, in DOM order (matches body-text order).
  const commenters = rawCommenters.filter((c) => c.handle !== author);

  // Comment text + likes via body-text parsing. Each comment renders as:
  //   <handle> <time> <comment text> 回复 查看所有N条回复 [<N>次赞]
  // Slice each comment's block from its handle to the next commenter's handle
  // so likes don't bleed across comments (a previous version matched the first
  // "次赞" anywhere after the handle, giving every comment the same like count).
  const timeToken = '(?:\\\\d+[.,]?\\\\d*\\\\s*(?:天|小时|周|分钟|秒|minutes?|hours?|days?|weeks?|seconds?|y|yr|years?))';
  const boundary = '(?:\\\\s+(?:回复|查看所有|次赞|likes?|replies?|responses?))';
  for (let i = 0; i < commenters.length && result.comments.length < 40; i++) {
    const c = commenters[i];
    const esc = c.handle.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
    const startIdx = bodyText.indexOf(c.handle);
    let blockEnd = bodyText.length;
    if (i + 1 < commenters.length) {
      const nextIdx = bodyText.indexOf(commenters[i + 1].handle, startIdx + c.handle.length);
      if (nextIdx > startIdx) blockEnd = nextIdx;
    }
    const block = startIdx >= 0 ? bodyText.slice(startIdx, blockEnd) : '';

    let commentText = '';
    if (block) {
      try {
        const re = new RegExp(esc + '\\\\s+' + timeToken + '\\\\s+(.+?)' + boundary, 'i');
        const m = block.match(re);
        if (m && m[1]) commentText = m[1].trim().slice(0, 500);
      } catch (e) {}
    }
    // Likes: scoped to this comment's block only.
    let likes = 0;
    if (block) {
      const lm = block.match(/([\\d.,]+)\\s*(?:次赞|赞|likes?)/i);
      if (lm && lm[1]) likes = parseInt(lm[1].replace(/,/g, '')) || 0;
    }
    result.comments.push({ author_handle: c.handle, text: commentText, likes });
    result.commenters.push({
      handle: c.handle,
      profile_url: c.href,
      comment_text: commentText.slice(0, 200),
      is_verified: c.is_verified,
      followers_hint: null,
    });
  }

  result.total_visible = result.comments.length;
  return result;
})()
"""


def fetch_reel_comments(
    runner,
    reel_url: str,
    mode: str = "evaluation",
    max_items: int = 15,
    include_caption: bool = True,
    min_followers_hint: int = 100000,
    scroll_comments: int = 0,
) -> dict:
    """Navigate to a Reel page and extract comments.

    Args:
        runner: A ``CdpRunner`` instance.
        reel_url: IG Reel URL.
        mode: ``"evaluation"`` (comments+caption) or ``"discovery"`` (commenter handles).
        max_items: Max items to return.
        include_caption: Whether to include caption/hashtags (evaluation mode).
        min_followers_hint: Discovery mode — min follower count for commenter filter.
        scroll_comments: Number of times to scroll comments section (default 0 = first viewport only).

    Returns:
        Dict with comments data (shape depends on mode).

    Raises:
        DomChangedError: If JS extraction returns empty data.
        CheckpointError: If risk page detected.
        SessionExpiredError: If login wall detected.
    """
    import pacing

    pacing.jitter_delay("reel")
    pacing.mark_reel_load(runner.task_id)

    resp = runner.navigate(reel_url)
    if not resp.get("success"):
        raise DomChangedError(f"navigate to reel failed: {resp.get('error')}")

    data = resp.get("data", {})
    snapshot_text = data.get("snapshot", "")
    raise_on_risk(snapshot_text)

    # The reel page's comment section hydrates after the video player loads —
    # evaluating immediately returns 0 commenters (the caption survives because
    # it comes from og:description, but the commenter handle links aren't in the
    # DOM yet). Settle ~1.2s before extracting.
    import random as _random
    import time as _time
    _time.sleep(_random.uniform(1.0, 1.5))

    # Optional scroll to load more comments (default 0 = first viewport only)
    for _ in range(scroll_comments):
        runner.scroll(1)
        _time.sleep(0.4)

    js_result = runner.eval(_COMMENTS_JS)
    if not js_result or not isinstance(js_result, dict):
        raise DomChangedError("JS comments extraction returned empty or non-dict result")

    if mode == "evaluation":
        comments = js_result.get("comments", [])[:max_items]
        return {
            "data": {
                "reel_url": reel_url,
                "caption": js_result.get("caption", "") if include_caption else "",
                "hashtags": js_result.get("hashtags", []) if include_caption else [],
                "thumbnail_url": js_result.get("thumbnail_url", ""),
                "reel_likes": js_result.get("reel_likes", 0),
                "reel_comments_count": js_result.get("reel_comments_count", 0),
                "comments": comments,
                "total_visible": len(comments),
            },
            "errors": [],
        }
    else:  # discovery mode
        commenters = js_result.get("commenters", [])[:max_items]
        return {
            "data": {
                "reel_url": reel_url,
                "reel_likes": js_result.get("reel_likes", 0),
                "reel_comments_count": js_result.get("reel_comments_count", 0),
                "commenters": commenters,
                "total_visible": len(commenters),
            },
            "errors": [],
        }
