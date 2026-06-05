import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { goalLabel } from '../constants/domainLabels';
import { errorSummary } from '../lib/errors';
import { toast } from '../lib/store';

type Sample = {
  event_id?: number;
  identity_id?: number;
  campaign_id?: string;
  goal?: string;
  ts?: string;
  edit_distance?: number;
  child_skill?: string;
};

type Preview = {
  ready?: boolean;
  skipped?: boolean;
  reason?: string;
  batch_threshold?: number;
  sample_count?: number;
  sample_identity_count?: number;
  remaining_after_batch?: number;
  edited_available?: number | unknown[];
  pending_edits?: number;
  edited_queued_in_pending?: number;
  samples?: Sample[];
};

function editedAvailableCount(preview: Preview): number {
  if (typeof preview.pending_edits === 'number') return preview.pending_edits;
  const raw = preview.edited_available;
  if (typeof raw === 'number') return raw;
  if (Array.isArray(raw)) return raw.length;
  return 0;
}

export function LearningNextBatchPreview({
  env,
  scope = 'company_style',
}: {
  env: string;
  scope?: 'company_style' | 'user_style';
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const qs = new URLSearchParams({ env, scope });
      const out = await api.get<Preview>(
        `/learning/preview-edit-batch?${qs.toString()}`,
      );
      setPreview(out);
    } catch (ex) {
      toast.error('加载失败', errorSummary(ex));
    } finally {
      setBusy(false);
    }
  }, [env, scope]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!preview) return null;

  return (
    <div className="rounded border border-slate-200 bg-slate-50/80 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-slate-800">下一批蒸馏样本预览</span>
        <button
          type="button"
          disabled={busy}
          onClick={() => void load()}
          className="ml-auto text-[11px] text-sky-700 underline disabled:opacity-40"
        >
          {busy ? '刷新中…' : '刷新'}
        </button>
      </div>
      <p className="mt-1 text-[11px] text-slate-500">
        只读：生成学习提案时将取下列编辑事件（默认最新 {preview.batch_threshold ?? 10}{' '}
        条，可跨多位 KOL）。
        {preview.edited_queued_in_pending
          ? ` 另有 ${preview.edited_queued_in_pending} 条已在待审批提案中。`
          : ''}
      </p>
      {preview.skipped && !preview.ready && (
        <p className="mt-2 text-[11px] text-amber-800">
          暂不可生成：{preview.reason === 'below_style_learning_batch_threshold'
            ? `仅 ${editedAvailableCount(preview)} 条可蒸馏样本，未达阈值 ${preview.batch_threshold}`
            : preview.reason ?? '未知'}
        </p>
      )}
      {preview.ready && preview.samples && preview.samples.length > 0 && (
        <ul className="mt-2 max-h-40 space-y-1 overflow-auto text-[11px] text-slate-700">
          {preview.samples.map((s) => (
            <li key={s.event_id} className="rounded border border-slate-100 bg-white px-2 py-1">
              KOL {s.identity_id} · {s.campaign_id} · {goalLabel(s.goal)}
              {s.edit_distance != null && (
                <> · 编辑幅度 {Math.round(Number(s.edit_distance) * 100)}%</>
              )}
              {s.ts && <> · {s.ts}</>}
            </li>
          ))}
        </ul>
      )}
      {preview.ready && (
        <p className="mt-2 text-[11px] text-slate-600">
          本批 {preview.sample_count} 条 · {preview.sample_identity_count} 位 KOL · 批准后剩余约{' '}
          {preview.remaining_after_batch} 条可蒸馏
        </p>
      )}
    </div>
  );
}
