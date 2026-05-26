import { useEffect, useState } from 'react';
import { api } from '../../api';
import { errorSummary } from '../../lib/errors';
import { toast } from '../../lib/store';
import { MANUAL_OUTCOMES, outcomeDef, type KolOutcome } from '../../lib/kolOutcomes';

type Props = {
  open: boolean;
  identityId: number;
  campaignId: string;
  displayName: string;
  env: 'TEST' | 'LIVE';
  onClose: () => void;
  // Called after a successful archive so the caller can refresh data /
  // navigate away.
  onArchived?: (outcome: KolOutcome) => void;
};

export function KolArchiveDialog({
  open,
  identityId,
  campaignId,
  displayName,
  env,
  onClose,
  onArchived,
}: Props) {
  const [outcome, setOutcome] = useState<KolOutcome | ''>('');
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setOutcome('');
      setNote('');
      setSubmitting(false);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, submitting, onClose]);

  if (!open) return null;

  const def = outcome ? outcomeDef(outcome) : null;
  const isLive = env === 'LIVE';
  const submit = async () => {
    if (!outcome || submitting) return;
    setSubmitting(true);
    try {
      // 1) Write identity-level relationship outcome (affects future
      //    discovery skill checks via last_outcome).
      await api.post(`/identities/${identityId}/archive`, {
        campaign_id: campaignId,
        outcome,
        notes: note,
      });
      // 2) Flip the per-campaign candidate row to candidate_status='archived'
      //    so the kanban stops showing the card in active lanes. Without
      //    this, the relationship is archived but the candidate row keeps
      //    its old status (discovered / selected_for_outreach / …) and
      //    the card stays visible.
      await api.post(`/campaigns/${encodeURIComponent(campaignId)}/candidates/status`, {
        identity_ids: [identityId],
        candidate_status: 'archived',
        review_reason: note ? `${outcome}: ${note}` : outcome,
        env,
      });
      toast.success('已归档', `${displayName || `#${identityId}`} → ${def?.label ?? outcome}`);
      onArchived?.(outcome);
      onClose();
    } catch (ex) {
      toast.error('归档失败', errorSummary(ex));
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4 py-6"
      role="dialog"
      aria-modal="true"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <form
        className="w-full max-w-md rounded-lg bg-white shadow-xl"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <div className="border-b border-slate-200 px-4 py-3 text-sm font-semibold text-slate-800">
          归档此 KOL
          <div className="mt-0.5 text-xs font-normal text-slate-500">
            {displayName || `identity #${identityId}`} · campaign {campaignId}
          </div>
        </div>

        <div className="space-y-3 px-4 py-3 text-sm text-slate-700">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">
              归档原因 <span className="text-rose-600">*</span>
            </label>
            <div className="space-y-1.5">
              {MANUAL_OUTCOMES.map((o) => (
                <label
                  key={o.value}
                  className={`flex cursor-pointer items-start gap-2 rounded border px-2.5 py-2 text-sm transition ${
                    outcome === o.value
                      ? 'border-emerald-400 bg-emerald-50'
                      : 'border-slate-200 hover:border-slate-300'
                  }`}
                >
                  <input
                    type="radio"
                    name="archive-outcome"
                    value={o.value}
                    checked={outcome === o.value}
                    onChange={() => setOutcome(o.value)}
                    className="mt-0.5"
                  />
                  <span className="flex-1">
                    <span className={o.tone === 'rose' ? 'font-medium text-rose-700' : 'text-slate-800'}>
                      {o.label}
                    </span>
                    {o.hint && (
                      <span className="block text-xs text-slate-500">{o.hint}</span>
                    )}
                  </span>
                </label>
              ))}
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-slate-600">
              备注（可选）
            </label>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              placeholder="例如：bio 挂着自家家具店链接"
              className="w-full rounded border border-slate-300 px-2 py-1 text-sm focus:border-emerald-400 focus:outline-none"
            />
          </div>

          {isLive && (
            <div className="rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs text-amber-800">
              <span className="font-semibold">LIVE 环境</span>
              ：此操作会写入正式数据。归档后可在「KOL归档」页找回。
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-200 bg-slate-50 px-4 py-2">
          <button
            type="button"
            className="rounded border border-slate-300 bg-white px-3 py-1 text-sm text-slate-700 hover:bg-slate-100 disabled:opacity-40"
            onClick={onClose}
            disabled={submitting}
          >
            取消
          </button>
          <button
            type="submit"
            disabled={!outcome || submitting}
            className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-700 focus:outline-none focus:ring-2 focus:ring-emerald-400 disabled:opacity-40"
          >
            {submitting ? '归档中…' : '确认归档'}
          </button>
        </div>
      </form>
    </div>
  );
}
