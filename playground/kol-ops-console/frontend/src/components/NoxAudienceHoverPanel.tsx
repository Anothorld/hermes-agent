import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { NoxInsightsSections } from './NoxInsightsSections';

const POPOVER_W = 380;
const POPOVER_MAX_H = 420;
const HOVER_OPEN_MS = 200;
const HOVER_CLOSE_MS = 180;

type PanelProps = {
  facts: Record<string, unknown>;
};

/** Audience-only Nox charts — same data as KOL detail diligence panel. */
export function NoxAudienceHoverPanel({ facts }: PanelProps) {
  const hasAudience = Object.keys(facts).some(
    (k) =>
      k.startsWith('identity.nox_audience_')
      || k === 'identity.nox_top_region'
      || k === 'identity.nox_gender_skew'
      || k === 'identity.region',
  );

  if (!hasAudience) {
    return (
      <div className="px-3 py-2 text-xs text-slate-500">
        暂无受众画像数据（需完成 Nox 尽调）
      </div>
    );
  }

  return (
    <div className="max-h-[min(70vh,420px)] overflow-x-hidden overflow-y-auto p-2">
      <NoxInsightsSections facts={facts} categoryIds={['audience']} variant="popover" />
    </div>
  );
}

type HoverButtonProps = {
  facts: Record<string, unknown>;
};

/** Table-cell trigger: portal + fixed popover avoids overflow clipping. */
export function AudienceProfileHoverButton({ facts }: HoverButtonProps) {
  const popoverId = useId();
  const anchorRef = useRef<HTMLSpanElement>(null);
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });

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
    if (left + POPOVER_W + margin > window.innerWidth) {
      left = Math.max(margin, window.innerWidth - POPOVER_W - margin);
    }
    let top = rect.bottom + margin;
    if (top + POPOVER_MAX_H + margin > window.innerHeight) {
      top = Math.max(margin, rect.top - POPOVER_MAX_H - margin);
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
    closeTimer.current = setTimeout(() => setOpen(false), HOVER_CLOSE_MS);
  }, [clearTimers]);

  useEffect(() => () => clearTimers(), [clearTimers]);

  useEffect(() => {
    if (!open) return;
    const onReflow = () => updatePosition();
    window.addEventListener('scroll', onReflow, true);
    window.addEventListener('resize', onReflow);
    return () => {
      window.removeEventListener('scroll', onReflow, true);
      window.removeEventListener('resize', onReflow);
    };
  }, [open, updatePosition]);

  const popover =
    open &&
    createPortal(
      <div
        id={popoverId}
        role="tooltip"
        className="fixed z-[200] overflow-hidden rounded-lg border border-violet-200 bg-white shadow-xl"
        style={{ top: pos.top, left: pos.left, width: POPOVER_W, maxHeight: POPOVER_MAX_H }}
        onMouseEnter={() => {
          clearTimers();
          setOpen(true);
        }}
        onMouseLeave={scheduleClose}
      >
        <div className="border-b border-violet-100 bg-violet-50/80 px-2.5 py-1.5 text-[10px] font-medium text-violet-900">
          受众画像
        </div>
        <NoxAudienceHoverPanel facts={facts} />
      </div>,
      document.body,
    );

  return (
    <>
      <span
        ref={anchorRef}
        className="inline-block"
        onMouseEnter={scheduleOpen}
        onMouseLeave={scheduleClose}
      >
        <button
          type="button"
          className="rounded border border-violet-200 bg-violet-50 px-2 py-0.5 text-xs text-violet-800 hover:bg-violet-100"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? popoverId : undefined}
        >
          查看画像
        </button>
      </span>
      {popover}
    </>
  );
}
