type EditLearningPayload = {
  was_edited?: boolean;
  edit_distance?: number;
  normalized_agent_body?: string;
  normalized_sent_body?: string;
  child_skill?: string;
  goal?: string;
  sent_message_id?: string;
};

export type DraftEditDiffPanelProps = {
  agentBody?: string;
  editLearning?: EditLearningPayload | null;
  title?: string;
};

/**
 * Side-by-side preview of agent draft vs operator final sent body.
 * Data comes from reconcile-sent ``draft_edit_learning`` payload or
 * inline fields on an approved/sent approval row.
 */
export default function DraftEditDiffPanel({
  agentBody,
  editLearning,
  title = 'Agent 稿 vs 运营终稿',
}: DraftEditDiffPanelProps) {
  const payload = editLearning ?? null;
  const wasEdited = payload?.was_edited === true;
  const agent = payload?.normalized_agent_body || agentBody || '';
  const sent = payload?.normalized_sent_body || '';
  const distance = payload?.edit_distance;

  if (!agent && !sent) {
    return null;
  }

  return (
    <div className="rounded border border-amber-200 bg-amber-50/40">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-100 px-3 py-2">
        <div className="text-xs font-medium text-amber-900">{title}</div>
        <div className="flex flex-wrap gap-2 text-[10px] text-amber-800">
          {wasEdited ? (
            <span className="rounded bg-amber-100 px-1.5 py-0.5">已编辑</span>
          ) : (
            <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-emerald-800">未改动</span>
          )}
          {distance != null && (
            <span className="rounded bg-white px-1.5 py-0.5">edit_distance={distance}</span>
          )}
          {payload?.child_skill && (
            <span className="font-mono">{payload.child_skill}</span>
          )}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-0 md:grid-cols-2">
        <div className="border-b border-amber-100 p-3 md:border-b-0 md:border-r">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-slate-500">
            Agent 草稿
          </div>
          <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words font-sans text-[12px] text-slate-800">
            {agent || '—'}
          </pre>
        </div>
        <div className="p-3">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-slate-500">
            运营终稿（Gmail SENT）
          </div>
          <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words font-sans text-[12px] text-slate-800">
            {sent || '—'}
          </pre>
        </div>
      </div>
    </div>
  );
}
