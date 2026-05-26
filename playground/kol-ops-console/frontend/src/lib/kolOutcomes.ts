// Shared outcome/archive-reason vocabulary for the KOL ops console.
//
// `last_outcome` is open-ended on the backend (cal.archive_collab accepts any
// string), but the console centralises the known values here so the archive
// modal, the archive page filter, and the kanban tag styling stay in sync.
//
// Axis: most values describe how a campaign-level engagement ended; the
// `do-not-contact` group (currently just `competitor`) describes the KOL
// itself as unsuitable across all future campaigns. Discovery skills should
// hard-skip handles whose `last_outcome` is in DO_NOT_CONTACT_OUTCOMES.

export type KolOutcome =
  | 'success'
  | 'legacy_collab'
  | 'incomplete'
  | 'aborted'
  | 'outreach_only'
  | 'declined'
  | 'competitor';

export type OutcomeDef = {
  value: KolOutcome;
  label: string;
  // Manual = operator picks it from the archive modal.
  // Auto = system writes it (e.g. archival-writer skill on campaign close).
  manual: boolean;
  // Tailwind tag color for chips/badges.
  tone: 'emerald' | 'amber' | 'sky' | 'slate' | 'rose';
  hint?: string;
};

export const OUTCOMES: OutcomeDef[] = [
  {
    value: 'competitor',
    label: '竞品 — 不合作',
    manual: true,
    tone: 'rose',
    hint: '此KOL自营家具/属于品牌竞品，发现流程会跳过',
  },
  {
    value: 'declined',
    label: '无合作意向',
    manual: true,
    tone: 'amber',
    hint: '明确拒绝或长期无回应',
  },
  {
    value: 'aborted',
    label: '主动叫停',
    manual: true,
    tone: 'amber',
    hint: '我方主动终止本次合作',
  },
  {
    value: 'incomplete',
    label: '沟通中断',
    manual: true,
    tone: 'slate',
    hint: '沟通过但未推进到交付',
  },
  {
    value: 'outreach_only',
    label: '仅外联',
    manual: false,
    tone: 'sky',
  },
  {
    value: 'success',
    label: '已合作完成',
    manual: false,
    tone: 'emerald',
  },
  {
    value: 'legacy_collab',
    label: '历史合作',
    manual: false,
    tone: 'slate',
  },
];

export const MANUAL_OUTCOMES = OUTCOMES.filter((o) => o.manual);

// Outcomes that mean "never contact this identity again", checked by the
// discovery skills before recommending a handle.
export const DO_NOT_CONTACT_OUTCOMES: KolOutcome[] = ['competitor'];

export function outcomeDef(value: string | null | undefined): OutcomeDef | null {
  if (!value) return null;
  return OUTCOMES.find((o) => o.value === value) ?? null;
}

export function outcomeLabel(value: string | null | undefined): string {
  return outcomeDef(value)?.label ?? value ?? '';
}

// Tailwind text-color class for inline outcome labels (matches the legacy
// outcomeClass() helper that used to live in KolArchivePage).
export function outcomeTextClass(value: string | null | undefined): string {
  const def = outcomeDef(value);
  if (!def) return 'text-slate-500';
  switch (def.tone) {
    case 'emerald':
      return 'text-emerald-700';
    case 'amber':
      return 'text-amber-700';
    case 'sky':
      return 'text-sky-700';
    case 'rose':
      return 'text-rose-700';
    default:
      return 'text-slate-700';
  }
}

// Tailwind chip class (bg + border + text) for compact KOL-card tags.
export function outcomeChipClass(value: string | null | undefined): string {
  const def = outcomeDef(value);
  if (!def) return 'border-slate-200 bg-slate-50 text-slate-600';
  switch (def.tone) {
    case 'emerald':
      return 'border-emerald-200 bg-emerald-50 text-emerald-700';
    case 'amber':
      return 'border-amber-200 bg-amber-50 text-amber-700';
    case 'sky':
      return 'border-sky-200 bg-sky-50 text-sky-700';
    case 'rose':
      return 'border-rose-200 bg-rose-50 text-rose-700';
    default:
      return 'border-slate-200 bg-slate-50 text-slate-700';
  }
}
