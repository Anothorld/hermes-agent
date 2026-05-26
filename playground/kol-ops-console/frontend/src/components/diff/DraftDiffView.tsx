interface DraftPayload {
  subject?: string | null;
  body?: string | null;
}

interface Props {
  // Previous draft — left column. When omitted, the right column is
  // rendered alone so the component is still useful as a "current
  // draft preview".
  previous?: DraftPayload | null;
  // Current draft — right column. Required.
  current: DraftPayload;
  // Optional caption above each column (defaults: "上一版" / "当前版").
  previousLabel?: string;
  currentLabel?: string;
}

// Side-by-side draft diff. Intentionally not line-by-line — for
// reply_draft the operator wants to read both versions in full, with
// the same scroll viewport, before deciding to approve. Differences
// pop out by visual comparison; if a real diff library proves
// necessary later we can swap it in without touching call sites.

export function DraftDiffView({
  previous,
  current,
  previousLabel = '上一版',
  currentLabel = '当前版',
}: Props) {
  if (!previous) {
    return (
      <div className="rounded border border-slate-200 bg-white">
        <div className="border-b border-slate-100 bg-slate-50 px-3 py-1.5 text-xs font-medium uppercase tracking-wide text-slate-600">
          {currentLabel}
        </div>
        <div className="space-y-2 p-3 text-sm">
          <DraftBlock label="subject" value={current.subject} />
          <DraftBlock label="body" value={current.body} multiline />
        </div>
      </div>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
      <Column label={previousLabel} draft={previous} tone="muted" />
      <Column label={currentLabel} draft={current} tone="accent" />
    </div>
  );
}

function Column({
  label,
  draft,
  tone,
}: {
  label: string;
  draft: DraftPayload;
  tone: 'muted' | 'accent';
}) {
  const headerCls =
    tone === 'accent'
      ? 'border-b border-emerald-200 bg-emerald-50 text-emerald-800'
      : 'border-b border-slate-200 bg-slate-50 text-slate-600';
  return (
    <div className="overflow-hidden rounded border border-slate-200 bg-white">
      <div className={`${headerCls} px-3 py-1.5 text-xs font-medium uppercase tracking-wide`}>
        {label}
      </div>
      <div className="space-y-2 p-3 text-sm">
        <DraftBlock label="subject" value={draft.subject} />
        <DraftBlock label="body" value={draft.body} multiline />
      </div>
    </div>
  );
}

function DraftBlock({
  label,
  value,
  multiline = false,
}: {
  label: string;
  value: string | null | undefined;
  multiline?: boolean;
}) {
  return (
    <div>
      <div className="mb-0.5 text-[11px] uppercase tracking-wide text-slate-500">
        {label}
      </div>
      {value == null || value === '' ? (
        <div className="text-xs italic text-slate-400">(空)</div>
      ) : multiline ? (
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-50 p-2 text-xs leading-snug text-slate-800">
          {value}
        </pre>
      ) : (
        <div className="break-words text-sm leading-snug text-slate-800">
          {value}
        </div>
      )}
    </div>
  );
}
