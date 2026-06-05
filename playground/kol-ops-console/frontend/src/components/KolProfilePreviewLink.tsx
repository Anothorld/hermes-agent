import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api';
import {
  linkPreviewFromFacts,
  type KolProfilePreviewSnapshot,
  type LinkPreviewPayload,
} from '../lib/kolProfileSnapshot';

const PREVIEW_W = 320;
const PREVIEW_H = 420;
const HOVER_OPEN_MS = 280;
const HOVER_CLOSE_MS = 220;

/** Platforms that block iframe embed (X-Frame-Options). */
function embedLikelyBlocked(url: string): boolean {
  try {
    const host = new URL(url).hostname.toLowerCase();
    return (
      host.includes('instagram.com')
      || host.includes('tiktok.com')
      || host.includes('facebook.com')
      || host === 'x.com'
      || host.includes('twitter.com')
      || host.includes('threads.net')
      || host.includes('threads.com')
    );
  } catch {
    return false;
  }
}

function SnapshotLines({ snapshot }: { snapshot: KolProfilePreviewSnapshot }) {
  const lines: { label: string; value: string }[] = [];
  if (snapshot.handle) lines.push({ label: '账号', value: `@${snapshot.handle.replace(/^@/, '')}` });
  if (snapshot.displayName) lines.push({ label: '名称', value: snapshot.displayName });
  if (snapshot.followers) {
    lines.push({
      label: '粉丝',
      value: snapshot.followersNote
        ? `${snapshot.followers}（${snapshot.followersNote}）`
        : snapshot.followers,
    });
  }
  if (snapshot.noxVerdict) lines.push({ label: 'Nox', value: snapshot.noxVerdict });
  if (lines.length === 0) return null;
  return (
    <ul className="w-full space-y-1 text-left text-[11px] text-slate-600">
      {lines.map((row) => (
        <li key={row.label}>
          <span className="font-medium text-slate-700">{row.label}：</span>
          {row.value}
        </li>
      ))}
    </ul>
  );
}

function BlockedEmbedPreview({
  url,
  label,
  snapshot,
  og,
  ogLoading,
}: {
  url: string;
  label: string;
  snapshot?: KolProfilePreviewSnapshot;
  og: LinkPreviewPayload | null;
  ogLoading: boolean;
}) {
  const title = og?.title ?? (snapshot?.displayName ? `@${snapshot.handle ?? ''}` : null);
  const description = og?.description ?? snapshot?.description ?? null;
  const image = og?.image ?? null;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {image ? (
        <div className="relative h-40 shrink-0 bg-gradient-to-br from-purple-500 via-pink-500 to-orange-400">
          <img
            src={image}
            alt=""
            className="h-full w-full object-cover"
            referrerPolicy="no-referrer"
          />
        </div>
      ) : (
        <div className="flex h-28 shrink-0 items-center justify-center bg-gradient-to-br from-purple-100 via-pink-50 to-orange-50">
          {ogLoading ? (
            <span className="text-xs text-slate-500">正在拉取主页摘要…</span>
          ) : (
            <span className="rounded-full bg-white/80 px-3 py-1 text-[11px] font-medium text-slate-700">
              {label}
            </span>
          )}
        </div>
      )}
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-auto px-3 py-2">
        {title && (
          <p className="line-clamp-2 text-xs font-semibold text-slate-900">{title}</p>
        )}
        {description && (
          <p className="line-clamp-4 text-[11px] leading-snug text-slate-600">{description}</p>
        )}
        {snapshot && <SnapshotLines snapshot={snapshot} />}
        {!ogLoading && !image && !title && !snapshot && (
          <p className="text-center text-xs text-slate-500">
            该平台不允许内嵌网页。正在尝试摘要预览；也可点击下方打开完整主页。
          </p>
        )}
        <p className="mt-auto break-all font-mono text-[9px] text-slate-400">{url}</p>
      </div>
    </div>
  );
}

type Variant = 'chip' | 'icon' | 'text';

/**
 * Hover to preview KOL profile; IG/TikTok use OG card + CAL snapshot (no iframe).
 */
export function KolProfilePreviewLink({
  url,
  label = '主页',
  variant = 'chip',
  className = '',
  stopPropagation = false,
  snapshot,
  initialLinkPreview,
  identityId,
  env = 'TEST',
  previewFacts,
}: {
  url: string | null | undefined;
  label?: string;
  variant?: Variant;
  className?: string;
  stopPropagation?: boolean;
  /** CAL / shortlist fields shown when iframe is blocked. */
  snapshot?: KolProfilePreviewSnapshot;
  /** Preloaded from shortlist API or CAL (skips hover fetch). */
  initialLinkPreview?: LinkPreviewPayload | null;
  identityId?: number | null;
  env?: string;
  previewFacts?: Record<string, unknown> | null;
}) {
  const popoverId = useId();
  const anchorRef = useRef<HTMLAnchorElement>(null);
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const [og, setOg] = useState<LinkPreviewPayload | null>(null);
  const [ogLoading, setOgLoading] = useState(false);
  const ogFetchRef = useRef<string | null>(null);

  const blockedEmbed = url ? embedLikelyBlocked(url) : false;

  const clearTimers = useCallback(() => {
    if (openTimer.current) clearTimeout(openTimer.current);
    if (closeTimer.current) clearTimeout(closeTimer.current);
    openTimer.current = null;
    closeTimer.current = null;
  }, []);

  const updatePosition = useCallback(() => {
    const rect = anchorRef.current?.getBoundingClientRect();
    if (!rect) return;
    const margin = 8;
    let left = rect.left;
    if (left + PREVIEW_W + margin > window.innerWidth) {
      left = Math.max(margin, window.innerWidth - PREVIEW_W - margin);
    }
    let top = rect.bottom + margin;
    if (top + PREVIEW_H + margin > window.innerHeight) {
      top = Math.max(margin, rect.top - PREVIEW_H - margin);
    }
    setPos({ top, left });
  }, []);

  const scheduleOpen = useCallback(() => {
    clearTimers();
    openTimer.current = setTimeout(() => {
      updatePosition();
      setOpen(true);
    }, HOVER_OPEN_MS);
  }, [clearTimers, updatePosition]);

  const scheduleClose = useCallback(() => {
    clearTimers();
    closeTimer.current = setTimeout(() => {
      setOpen(false);
      setOgLoading(false);
    }, HOVER_CLOSE_MS);
  }, [clearTimers]);

  useEffect(() => () => clearTimers(), [clearTimers]);

  useEffect(() => {
    if (!open) return;
    const onScroll = () => updatePosition();
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onScroll);
    return () => {
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onScroll);
    };
  }, [open, updatePosition]);

  useEffect(() => {
    setOg(null);
    ogFetchRef.current = null;
  }, [url]);

  useEffect(() => {
    if (!open || !url || !blockedEmbed) return;
    if (initialLinkPreview) {
      setOg(initialLinkPreview);
      setOgLoading(false);
      return;
    }
    const fromFacts = linkPreviewFromFacts(previewFacts, url);
    if (fromFacts) {
      setOg(fromFacts);
      setOgLoading(false);
      return;
    }
    if (ogFetchRef.current === url) return;
    let cancelled = false;
    ogFetchRef.current = url;
    setOgLoading(true);
    const qs = new URLSearchParams({ url, env });
    if (typeof identityId === 'number' && identityId > 0) {
      qs.set('identity_id', String(identityId));
    }
    void api
      .get<LinkPreviewPayload>(`/link-preview?${qs.toString()}`)
      .then((data) => {
        if (!cancelled) setOg(data);
      })
      .catch(() => {
        if (!cancelled) setOg({ ok: false });
      })
      .finally(() => {
        if (!cancelled) setOgLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    open,
    url,
    blockedEmbed,
    initialLinkPreview,
    previewFacts,
    identityId,
    env,
  ]);

  if (!url) return null;

  const triggerClass =
    variant === 'icon'
      ? 'inline-flex h-7 w-7 items-center justify-center rounded-full border border-sky-200 bg-sky-50 text-sky-800 hover:border-sky-400 hover:bg-sky-100'
      : variant === 'text'
        ? 'text-xs font-medium text-sky-800 underline-offset-2 hover:text-sky-900 hover:underline'
        : 'rounded border border-sky-200 bg-sky-50 px-2 py-0.5 text-xs font-medium text-sky-800 hover:border-sky-400 hover:bg-sky-100';

  const popover =
    open &&
    createPortal(
      <div
        id={popoverId}
        role="tooltip"
        className="fixed z-[200] overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl"
        style={{ top: pos.top, left: pos.left, width: PREVIEW_W }}
        onMouseEnter={() => {
          clearTimers();
          setOpen(true);
        }}
        onMouseLeave={scheduleClose}
      >
        <div className="border-b border-slate-100 bg-slate-50 px-2 py-1.5 text-[10px] text-slate-600">
          <span className="font-medium text-slate-800">{label}</span>
          <span className="ml-1 text-slate-500">
            · {blockedEmbed ? '摘要预览' : '悬停预览'} · 点击打开新标签页
          </span>
        </div>
        <div className="relative bg-slate-100" style={{ height: PREVIEW_H }}>
          {blockedEmbed ? (
            <BlockedEmbedPreview
              url={url}
              label={label}
              snapshot={snapshot}
              og={og}
              ogLoading={ogLoading}
            />
          ) : (
            <iframe
              title={`${label} 预览`}
              src={url}
              className="h-full w-full border-0 bg-white"
              sandbox="allow-scripts allow-same-origin allow-popups"
            />
          )}
        </div>
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="block border-t border-slate-100 px-2 py-1.5 text-center text-[11px] font-medium text-sky-800 hover:bg-sky-50"
        >
          在新标签页打开完整主页 →
        </a>
      </div>,
      document.body,
    );

  return (
    <>
      <a
        ref={anchorRef}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        aria-describedby={open ? popoverId : undefined}
        className={`${triggerClass} ${className}`.trim()}
        title={`${label} · ${url}`}
        onMouseEnter={scheduleOpen}
        onMouseLeave={scheduleClose}
        onFocus={scheduleOpen}
        onBlur={scheduleClose}
        onClick={(e) => {
          if (stopPropagation) e.stopPropagation();
        }}
      >
        {variant === 'icon' ? (
          <svg
            viewBox="0 0 20 20"
            className="h-4 w-4"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            aria-hidden
          >
            <path d="M3 10h14M10 3v14" strokeLinecap="round" />
            <circle cx="10" cy="10" r="7" />
          </svg>
        ) : (
          label
        )}
        <span className="sr-only">（新标签页打开）</span>
      </a>
      {popover}
    </>
  );
}
