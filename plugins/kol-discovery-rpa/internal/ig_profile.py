"""IG profile extraction — navigate to profile page and extract structured data.

Uses ``cdp_page.navigate_open`` + ``cdp_page.evaluate`` to extract:
- followers (raw text for normalization)
- following count
- post count
- bio text
- full name / display name
- is_business / professional category
- location signals (from bio + address)
- link-in-bio URLs (Linktree, Beacons, etc.)
- is_verified

Then runs profile-level qualification gates via ``qualify_evaluator``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Hyphenated directory can't use package imports
_INTERNAL_DIR = str(Path(__file__).resolve().parent)
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

from errors import DomChangedError  # noqa: E402
from qualify_evaluator import evaluate_profile_gates  # noqa: E402
from risk_detector import raise_on_risk  # noqa: E402


# JS to extract profile data from IG profile page DOM.
#
# LOCALE-AWARE + STRUCTURAL extraction. The local debug Chrome renders IG in
# the user's locale (e.g. zh-CN → "4.5万粉丝", "2361帖子", "3571关注"; en →
# "4.5万 followers"). An English-only regex silently returned followers='' and
# hard_discarded real KOLs whose page rendered fine. We now:
#   1. Word-match counts in <header> across locales (en/zh/es/pt/fr/de/ja/ko).
#   2. Fall back to a locale-independent STRUCTURAL scan: IG renders the three
#      stats as <span>count</span> inside a parent whose text is "count+word",
#      in the global order [posts, followers, following] — we read them by
#      position when the localized word isn't recognised.
#   3. Fall back to <meta> tags (locale-aware) for followers.
_PROFILE_JS = """
(() => {
  const text = (document.body && document.body.innerText) || '';
  const result = {
    handle: '',
    full_name: '',
    bio: '',
    followers_raw: '',
    following_raw: '',
    posts_count_raw: '',
    is_verified: false,
    is_business: null,
    professional_category: '',
    location_signals: [],
    bio_links: [],
    external_url: '',
    page_text_sample: text.slice(0, 3000),
    // 'dom_word' | 'dom_structural' | 'meta_description' | 'og_description' | ''
    extraction_source: '',
  };

  // Handle from URL
  const path = location.pathname.replace(/^\\//, '').replace(/\\/$/, '');
  result.handle = path.split('/')[0] || '';

  const headerEl = document.querySelector('header');
  const headerText = ((headerEl && headerEl.innerText) || '').replace(/\\s+/g, ' ').trim();
  const bodyText = text.replace(/\\s+/g, ' ').trim();

  // --- Structural scan (locale-independent) ---
  // IG renders stats as <span>count</span> whose parent text is "count+word"
  // (e.g. "4.5万粉丝", "123K followers"). Global order: posts, followers, following.
  const countOnlyRe = /^[\\d.,]+\\s*[KkMmBb万亿]?$/;
  const structuralCounts = [];
  if (headerEl) {
    for (const sp of headerEl.querySelectorAll('span')) {
      if (structuralCounts.length >= 3) break;
      const t = (sp.textContent || '').trim();
      if (!countOnlyRe.test(t) || t.length > 12) continue;
      const parent = sp.parentElement;
      if (!parent) continue;
      const pt = (parent.textContent || '').replace(/\\s+/g, ' ').trim();
      if (pt === t || !pt.startsWith(t)) continue;          // need a label after the count
      const label = pt.slice(t.length).replace(/\\s+/g, ' ').trim();
      if (!label || /^[\\d.,KkMmBb万亿\\s]+$/.test(label)) continue;  // label must be a word
      structuralCounts.push(t);
    }
  }
  // structuralCounts[0]=posts, [1]=followers, [2]=following

  // --- Word-based DOM extraction (locale-aware) ---
  // followers across locales; '位' (zh meta particle) tolerated between number and word.
  const followerWordRe = /([\\d.,]+)\\s*([KkMmBb万亿]?)\\s*位?\\s*(?:followers|follower|粉丝|粉絲|seguidores|seguidor|abonnés?|abonnes?|フォロワー|팔로워)/i;
  const followingWordRe = /([\\d.,]+)\\s*([KkMmBb万亿]?)\\s*(?:following|关注|seguidos|abonnements?|gefolgt|フォロー中|팔로잉)/i;
  const postsWordRe = /([\\d.,]+)\\s*篇?\\s*([KkMmBb万亿]?)\\s*(?:posts|帖子|publicaciones|publications|beiträge|beitrage|投稿|게시물)/i;

  let domFollowers = '', domFollowing = '', domPosts = '';
  {
    const m = (headerText.match(followerWordRe) || bodyText.match(followerWordRe));
    if (m) domFollowers = m[1] + (m[2] || '');
  }
  {
    const m = (headerText.match(followingWordRe) || bodyText.match(followingWordRe));
    if (m) domFollowing = m[1] + (m[2] || '');
  }
  {
    const m = (headerText.match(postsWordRe) || bodyText.match(postsWordRe));
    if (m) domPosts = m[1] + (m[2] || '');
  }

  // Resolve followers: DOM word → structural → meta → og
  if (domFollowers) {
    result.followers_raw = domFollowers; result.extraction_source = 'dom_word';
  } else if (structuralCounts.length >= 2) {
    result.followers_raw = structuralCounts[1]; result.extraction_source = 'dom_structural';
  }
  if (domFollowing) result.following_raw = domFollowing;
  else if (structuralCounts.length >= 3) result.following_raw = structuralCounts[2];
  if (domPosts) result.posts_count_raw = domPosts;
  else if (structuralCounts.length >= 1) result.posts_count_raw = structuralCounts[0];

  // --- Meta-tag fallback (locale-aware) for followers + bio/name ---
  const metaDesc = document.querySelector('meta[name="description"]');
  if (metaDesc) {
    const content = metaDesc.getAttribute('content') || '';
    if (!result.followers_raw) {
      const m = content.match(followerWordRe);
      if (m) { result.followers_raw = m[1] + (m[2] || ''); result.extraction_source = 'meta_description'; }
    }
    if (!result.following_raw) {
      const m = content.match(followingWordRe);
      if (m) result.following_raw = m[1] + (m[2] || '');
    }
    if (!result.posts_count_raw) {
      const m = content.match(postsWordRe);
      if (m) result.posts_count_raw = m[1] + (m[2] || '');
    }
  }

  // full_name: og:title primary (strip "(@handle)" + locale suffix like
  // "· Instagram 照片和视频" / "- Instagram photos and videos"); DOM h1 fallback.
  const ogTitle = document.querySelector('meta[property="og:title"]');
  if (ogTitle) {
    let name = (ogTitle.getAttribute('content') || '').replace(/\\s*\\(@[^)]+\\)\\s*/, '');
    name = name.split(/\\s*[·\\-]\\s*Instagram\\b/i)[0].trim();
    if (name) result.full_name = name;
  }
  if (!result.full_name && headerEl) {
    const h = headerEl.querySelector('h1, h2');
    if (h) result.full_name = (h.textContent || '').trim();
    else {
      const sp = headerEl.querySelector('section span');
      if (sp) result.full_name = (sp.textContent || '').trim();
    }
  }

  // bio: OG/meta description first (usually complete), DOM span fallback.
  const ogDesc = document.querySelector('meta[property="og:description"]');
  if (ogDesc) {
    const desc = ogDesc.getAttribute('content') || '';
    if (!result.followers_raw) {
      const m = desc.match(followerWordRe);
      if (m) { result.followers_raw = m[1] + (m[2] || ''); result.extraction_source = 'og_description'; }
    }
    if (!result.bio) {
      // Strip leading counts segment up to the separator. Handles en
      // "123 Followers, 456 Following, 789 Posts - bio" and zh
      // "45K 位粉丝、已关注 3,602 人、 2,361 篇帖子 - 查看…的 Instagram…"
      const bioPart = desc.replace(/^[\\d.,KkMmBb万亿\\s位篇人、，,]+\\s*(?:Followers|Following|Posts|粉丝|关注|帖子|篇帖子)[\\d.,KkMmBb万亿\\s位篇人、，,\\-]*\\s*[-·]\\s*/i, '');
      if (bioPart && bioPart !== desc) result.bio = bioPart.slice(0, 500);
    }
  }
  // Some locales put the bio in the description meta as a quoted trailing segment:
  // "…Instagram 用户 Caitlin Wilson (@handle)":"Founder, CEO, …"
  if (!result.bio && metaDesc) {
    const content = metaDesc.getAttribute('content') || '';
    const q = content.match(/["“”]\\s*([^"””]{15,})\\s*["””]\\s*$/);
    if (q) result.bio = q[1].slice(0, 500);
  }
  // DOM fallback for bio: longest header span that isn't counts/handle/name.
  if (!result.bio && headerEl) {
    let bestBio = '';
    for (const sp of headerEl.querySelectorAll('span')) {
      const t = (sp.innerText || '').trim();
      if (t.length < 10) continue;
      if (/followers|following|posts|粉丝|关注|帖子/i.test(t)) continue;
      if (t === result.handle || t === result.full_name) continue;
      if (/^[\\d.,KkMmBb万亿]+$/.test(t)) continue;
      if (t.length > bestBio.length) bestBio = t;
    }
    // Strip trailing truncation markers: "更多", "more", "..."
    bestBio = bestBio.replace(/\\s*(?:更多|more|\\.\\.\\.|…)\\s*$/i, '').trim();
    if (bestBio) result.bio = bestBio.slice(0, 500);
  }

  // Verified badge
  const verifiedImg = document.querySelector('img[alt="Verified"]');
  result.is_verified = !!verifiedImg;

  // Professional category (business accounts)
  const categoryEl = document.querySelector('[class*="professional_category"], header section [class*="category"]');
  if (categoryEl) {
    result.professional_category = categoryEl.textContent.trim();
    result.is_business = true;
  }

  // External URL (link-in-bio)
  const externalLink = document.querySelector('header a[href*="linktr.ee"], header a[href*="beacons.ai"], header a[href*="bio.link"], header a[href*="lnk.bio"], header a[href*="solo.to"], header a[href*="shopmy.us"], header a[href*="ltk.app"]');
  if (externalLink) {
    result.bio_links.push(externalLink.href);
    result.external_url = externalLink.href;
  }

  // Location signals from bio (city, state, country patterns)
  const bioLower = result.bio.toLowerCase();
  const locationPatterns = ['united states', 'usa', 'us', 'canada', 'ca', 'los angeles', 'new york', 'texas', 'california', 'chicago', 'miami', 'seattle', 'boston', 'denver', 'austin', 'portland', 'nashville', 'atlanta', 'san diego', 'san francisco', 'los angeles ca', 'nyc', 'la,', 'sf,', 'dc,'];
  for (const pat of locationPatterns) {
    if (bioLower.includes(pat)) {
      result.location_signals.push(pat);
    }
  }

  return result;
})()
"""


# --- Account location via the "..." → "账户简介" / "About this account" dialog ---
#
# The header "..." (Options) button opens a menu whose "账户简介" / "About this
# account" item opens a dialog showing the authoritative account country
# ("账户所在地": e.g. "美国" = United States), date joined, and verification date.
# This is more reliable than guessing region from bio text (e.g. a "📍SoCal" bio
# line resolved to region_unknown because "socal" matched no pattern, while the
# account dialog states the country directly). The dialog is locale-rendered
# (zh-CN: "账户简介" / "账户所在地"; en: "About this account" / "Account location").
#
# Each snippet is a standalone eval so the CdpRunner can sequence them with
# small settle delays between clicks (the menu/dialog open asynchronously).

_CLICK_OPTIONS_JS = """
(() => {
  const header = document.querySelector('header');
  if (!header) return {ok: false, error: 'no header'};
  for (const b of header.querySelectorAll('button, [role="button"]')) {
    const aria = b.getAttribute('aria-label') || '';
    const svg = b.querySelector('svg');
    const svgAria = svg ? (svg.getAttribute('aria-label') || '') : '';
    if (/options|选项|settings|设置|more|更多/i.test(aria || svgAria)) { b.click(); return {ok: true}; }
  }
  return {ok: false, error: 'options button not found'};
})()
"""

_CLICK_ABOUT_JS = """
(() => {
  const dialogs = document.querySelectorAll('[role="dialog"]');
  for (const d of dialogs) {
    for (const el of d.querySelectorAll('button, [role="menuitem"], [role="button"], a, li')) {
      const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
      const a = el.getAttribute('aria-label') || '';
      if (/账户简介|关于此账户|关于本账户|about this account|account details|account info/i.test(t + ' ' + a)) {
        el.click(); return {ok: true, via: t || a};
      }
    }
  }
  return {ok: false, error: 'about-this-account item not found'};
})()
"""

_READ_DETAILS_JS = """
(() => {
  const dialogs = document.querySelectorAll('[role="dialog"]');
  let details = null;
  for (const d of dialogs) {
    const t = (d.innerText || '');
    if (/账户所在地|所在地|加入日期|曾用账号|已认证|account location|date joined|former usernames|about this account/i.test(t)) {
      details = d; break;
    }
  }
  if (!details) return {ok: false, error: 'details dialog not found'};
  const text = (details.innerText || '').replace(/\\s+/g, ' ').trim();

  // Read a labeled row by finding its aria-labeled container, collecting leaf
  // text spans in DOM order, and taking the leaf AFTER the label as the value.
  // This is robust to the verified-date container which wraps label + date + a
  // long explanation paragraph (textContent-based strip grabbed all of it).
  const readRow = (labels) => {
    for (const label of labels) {
      const row = details.querySelector('[aria-label*=\"' + label + '\" i]');
      if (!row) continue;
      const lab = (row.getAttribute('aria-label') || '').trim();
      const leaves = [];
      for (const el of row.querySelectorAll('*')) {
        if (el.children.length === 0) {
          const t = (el.textContent || '').trim();
          if (t) leaves.push(t);
        }
      }
      // Find the leaf matching the label, take the next leaf as the value.
      for (let i = 0; i < leaves.length; i++) {
        if (leaves[i] === lab || leaves[i].includes(lab) || (lab && lab.includes(leaves[i]))) {
          if (i + 1 < leaves.length) return leaves[i + 1].slice(0, 80);
        }
      }
      // Fallback: first leaf that isn't the label itself.
      const other = leaves.find(l => l !== lab && !(lab && l.includes(lab)));
      if (other) return other.slice(0, 80);
    }
    // Regex fallback on dialog text — stop at the next known label keyword.
    const boundary = '(?:\\\\s+(?:加入日期|date joined|曾用账号|former usernames|已认证|verified|account location|所在地|关闭|close|获得认证|learn|详细了解|认证徽章))';
    for (const label of labels) {
      try {
        const re = new RegExp(label + '\\\\s*[:：]?\\\\s*(.+?)' + boundary, 'i');
        const m = text.match(re);
        if (m && m[1]) return m[1].trim().slice(0, 80);
      } catch (e) {}
    }
    return '';
  };

  return {
    ok: true,
    location: readRow(['账户所在地', '所在地', 'account location', 'location', '地区', '位置', 'country', '国家']),
    date_joined: readRow(['加入日期', 'date joined', 'joined', '注册时间']),
    verified_date: readRow(['已认证', 'verified', '认证日期']),
    former_usernames: readRow(['曾用账号', 'former usernames', 'previous usernames']),
    dialog_text_sample: text.slice(0, 400),
  };
})()
"""

_CLOSE_DIALOG_JS = """
(() => {
  const dialogs = document.querySelectorAll('[role="dialog"]');
  for (const d of dialogs) {
    for (const b of d.querySelectorAll('button, [role="button"]')) {
      const t = (b.textContent || '').replace(/\\s+/g, ' ').trim();
      const a = b.getAttribute('aria-label') || '';
      if (/^关闭$|close|关闭|done|完成|确定/i.test(t) || /close|关闭|done/i.test(a)) { b.click(); return {ok: true, via: 'button'}; }
    }
  }
  document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true}));
  return {ok: false, via: 'escape'};
})()
"""

# Locale-aware country name → ISO code. US/CA are gate-relevant; the rest are
# captured for operator visibility (the region gate still only passes US/CA).
_COUNTRY_MAP: dict[str, str] = {
    "美国": "US", "united states": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US",
    "america": "US", "estados unidos": "US", "états-unis": "US", "etats-unis": "US",
    "vereinigte staaten": "US", "アメリカ": "US", "アメリカ合衆国": "US", "미국": "US",
    "加拿大": "CA", "canada": "CA", "canadá": "CA", "kanada": "CA",
    "カナダ": "CA", "캐나다": "CA",
    "英国": "GB", "united kingdom": "GB", "uk": "GB", "inglaterra": "GB",
    "澳大利亚": "AU", "australia": "AU", "オーストラリア": "AU", "호주": "AU",
    "德国": "DE", "germany": "DE", "deutschland": "DE",
    "法国": "FR", "france": "FR",
    "西班牙": "ES", "spain": "ES", "españa": "ES",
    "意大利": "IT", "italy": "IT", "italia": "IT",
    "日本": "JP", "japan": "JP",
    "韩国": "KR", "south korea": "KR", "korea": "KR",
    "墨西哥": "MX", "mexico": "MX", "méxico": "MX",
    "巴西": "BR", "brazil": "BR", "brasil": "BR",
}


def _normalize_country(raw: str) -> str:
    """Normalize a locale country name (e.g. "美国", "United States") to ISO code."""
    if not raw:
        return ""
    key = raw.strip().lower()
    return _COUNTRY_MAP.get(key, "")


def _fetch_account_location(runner) -> dict:
    """Click header '...' → '账户简介' and read the account-location dialog.

    Returns a dict with ``location`` (raw, e.g. "美国"), ``country`` (ISO code,
    e.g. "US"), ``date_joined``, ``verified_date``, ``former_usernames``.
    Returns ``{}`` if the flow fails. Never raises — location is a best-effort
    enrichment; the region gate falls back to bio-derived signals when absent.
    """
    import time

    try:
        r = runner.eval(_CLICK_OPTIONS_JS)
        if not (isinstance(r, dict) and r.get("ok")):
            return {}
        time.sleep(0.5)

        r = runner.eval(_CLICK_ABOUT_JS)
        if not (isinstance(r, dict) and r.get("ok")):
            runner.eval(_CLOSE_DIALOG_JS)
            return {}
        time.sleep(0.7)

        r = runner.eval(_READ_DETAILS_JS)
        runner.eval(_CLOSE_DIALOG_JS)
        if not (isinstance(r, dict) and r.get("ok")):
            return {}
        location = (r.get("location") or "").strip()
        return {
            "location": location,
            "country": _normalize_country(location),
            "date_joined": (r.get("date_joined") or "").strip(),
            "verified_date": (r.get("verified_date") or "").strip(),
            "former_usernames": (r.get("former_usernames") or "").strip(),
        }
    except Exception:
        try:
            runner.eval(_CLOSE_DIALOG_JS)
        except Exception:
            pass
        return {}


def fetch_profile(runner, handle: str, include_bio_links: bool = True, include_account_location: bool = True) -> dict:
    """Navigate to an IG profile page and extract structured data + qualification.

    Args:
        runner: A ``CdpRunner`` instance.
        handle: IG handle (with or without @).
        include_bio_links: Whether to follow link-in-bio (default True; kept
            for API compatibility — currently extracts from profile page only).
        include_account_location: Whether to click the header "..." →
            "账户简介" / "About this account" dialog to read the authoritative
            account country ("账户所在地"). Adds ~2-3s + 2 clicks per profile.
            Default True — the country is far more reliable than bio guessing.

    Returns:
        Dict with profile ``data`` and ``qualification`` (profile-level gates).

    Raises:
        DomChangedError: If JS extraction returns empty data.
        CheckpointError: If risk page detected.
        SessionExpiredError: If login wall detected.
    """
    import pacing

    clean_handle = handle.lstrip("@")
    url = f"https://www.instagram.com/{clean_handle}/"

    pacing.jitter_delay("profile")
    pacing.mark_profile(runner.task_id)

    resp = runner.navigate(url)
    if not resp.get("success"):
        raise DomChangedError(f"navigate failed: {resp.get('error')}")

    data = resp.get("data", {})
    snapshot_text = data.get("snapshot", "")
    raise_on_risk(snapshot_text)

    # Extract structured data via JS
    js_result = runner.eval(_PROFILE_JS)
    if not js_result or not isinstance(js_result, dict):
        raise DomChangedError("JS extraction returned empty or non-dict result")

    page_text = (js_result.get("page_text_sample") or "").strip()
    page_lower = page_text.lower()

    # Locale-aware "follower signal" — the page rendered the profile stat block
    # if any locale's follower word appears. (Debug Chrome renders IG in the
    # user's locale, e.g. zh-CN → "粉丝", en → "followers". An English-only
    # check missed Chinese pages and produced false "no follower section" errors.)
    follower_signal_words = (
        "followers", "粉丝", "粉絲", "seguidores", "abonnés", "abonnes",
        "フォロワー", "팔로워",
    )
    has_follower_signal = any(w in page_text or w.lower() in page_lower for w in follower_signal_words)

    # SPA hydration race: IG sometimes serves the shell first and populates
    # counts via client-side JS after navigate_open's readyState check passes.
    # If the page shows a follower signal but we extracted none, settle briefly
    # and re-eval once. One retry only (~1s) so we don't double page-load time.
    if not js_result.get("followers_raw") and has_follower_signal:
        import random as _random
        import time as _time
        _time.sleep(_random.uniform(0.8, 1.2))
        js_result = runner.eval(_PROFILE_JS)
        if not js_result or not isinstance(js_result, dict):
            raise DomChangedError("JS extraction returned empty or non-dict result on retry")

    followers_raw = js_result.get("followers_raw")
    page_text = (js_result.get("page_text_sample") or "").strip()
    page_lower = page_text.lower()
    has_follower_signal = any(w in page_text or w.lower() in page_lower for w in follower_signal_words)

    # Empty render: no follower data AND no page text → blank page / nav failure
    if not followers_raw and not page_text:
        raise DomChangedError("profile page rendered empty — no follower data")

    # Render/extraction mismatch: page has content but we could not extract a
    # follower count. This is the "browser shows the profile with followers but
    # RPA returns 0" case. Raise DomChangedError so the agent's one-shot browser
    # fallback token fires (per kol-bridge-agent-guard) instead of silently
    # returning hard_discard with followers=0 + region_unknown, which discarded
    # real KOLs (e.g. ~500K-follower accounts) as if they had no followers.
    if not followers_raw:
        if has_follower_signal:
            raise DomChangedError(
                "followers visible in DOM but not extractable — selector/meta "
                "mismatch or unrecognised locale; retry via browser fallback"
            )
        raise DomChangedError(
            "profile page rendered without a follower section — likely login "
            "wall or private account; verify via browser fallback"
        )

    # Account location via the "..." → "账户简介" dialog (authoritative country).
    # Best-effort: failures return {} and the region gate falls back to bio signals.
    account_location = _fetch_account_location(runner) if include_account_location else {}

    # Build profile data
    profile_data = {
        "handle": js_result.get("handle", clean_handle),
        "full_name": js_result.get("full_name", ""),
        "bio": js_result.get("bio", ""),
        "followers_raw": js_result.get("followers_raw", ""),
        "following_raw": js_result.get("following_raw", ""),
        "posts_count_raw": js_result.get("posts_count_raw", ""),
        "is_verified": js_result.get("is_verified", False),
        "is_business": js_result.get("is_business"),
        "professional_category": js_result.get("professional_category", ""),
        "location_signals": js_result.get("location_signals", []),
        "bio_links": js_result.get("bio_links", []),
        "external_url": js_result.get("external_url", ""),
        "profile_url": url,
        "extraction_source": js_result.get("extraction_source", ""),
        "account_location": account_location.get("location", ""),
        "account_country": account_location.get("country", ""),
        "account_date_joined": account_location.get("date_joined", ""),
        "account_verified_date": account_location.get("verified_date", ""),
    }

    # Region signals: the account-dialog country is authoritative — prepend it
    # so _resolve_region returns it first. When the country code is unmapped
    # (non-US/CA), pass the raw location string so the gate can still rule it
    # out (returns "unknown" → correct discard for non-allowed regions).
    # Falls back to bio-derived location_signals when the dialog wasn't read.
    region_signals = list(profile_data["location_signals"])
    if account_location.get("country"):
        region_signals.insert(0, account_location["country"])
    elif account_location.get("location"):
        region_signals.insert(0, account_location["location"])

    # Run profile-level qualification gates
    import qualification_rules as _rules
    qual = evaluate_profile_gates(
        followers_raw=profile_data["followers_raw"],
        region_signals=region_signals,
        bio=profile_data["bio"],
        bio_links=profile_data["bio_links"],
        is_business=profile_data["is_business"],
    )

    return {
        "data": profile_data,
        "qualification": {
            "hard_discard": len(qual["discard_reasons"]) > 0,
            "discard_reasons": qual["discard_reasons"],
            "gates": qual["gates"],
            "agent_judgment_required": _rules.AGENT_JUDGMENT_REQUIRED,
        },
    }
