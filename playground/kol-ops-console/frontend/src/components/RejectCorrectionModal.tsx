import { useMemo, useState } from 'react';
import {
  EMPTY_REJECT_CORRECTION,
  REJECT_TAG_LABELS,
  REJECT_TAGS,
  type RejectCorrection,
  type RejectTag,
} from '../constants/rejectTags';

export type RejectCorrectionModalProps = {
  open: boolean;
  loading?: boolean;
  onClose: () => void;
  onSubmit: (correction: RejectCorrection) => void | Promise<void>;
};

/**
 * Structured reject form for ``approval.reply_draft``.
 * Posts ``correction: { tags, note, suggested_fix }`` to Bridge reject API.
 */
export default function RejectCorrectionModal({
  open,
  loading = false,
  onClose,
  onSubmit,
}: RejectCorrectionModalProps) {
  const [tags, setTags] = useState<RejectTag[]>(['other']);
  const [note, setNote] = useState('');
  const [suggestedFix, setSuggestedFix] = useState('');

  const canSubmit = useMemo(() => tags.length > 0 && !loading, [tags, loading]);

  if (!open) return null;

  const toggleTag = (tag: RejectTag) => {
    setTags((prev) => {
      if (prev.includes(tag)) {
        const next = prev.filter((t) => t !== tag);
        return next.length ? next : ['other'];
      }
      return [...prev, tag];
    });
  };

  const reset = () => {
    setTags([...EMPTY_REJECT_CORRECTION.tags]);
    setNote('');
    setSuggestedFix('');
  };

  const handleSubmit = async () => {
    await onSubmit({ tags, note: note.trim(), suggested_fix: suggestedFix.trim() });
    reset();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="reject-modal-title"
        className="w-full max-w-lg rounded-lg border border-slate-200 bg-white shadow-xl"
      >
        <div className="border-b border-slate-100 px-4 py-3">
          <h2 id="reject-modal-title" className="text-sm font-semibold text-slate-900">
            驳回草稿 — 结构化反馈
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            标签与备注会写入 learning 事件，供 dispatcher 作为 negative few-shot。
          </p>
        </div>

        <div className="space-y-4 px-4 py-3">
          <div>
            <div className="mb-1.5 text-xs font-medium text-slate-700">问题标签（可多选）</div>
            <div className="flex flex-wrap gap-1.5">
              {REJECT_TAGS.map((tag) => {
                const active = tags.includes(tag);
                return (
                  <button
                    key={tag}
                    type="button"
                    onClick={() => toggleTag(tag)}
                    className={`rounded-full border px-2.5 py-1 text-[11px] transition ${
                      active
                        ? 'border-rose-300 bg-rose-50 text-rose-900'
                        : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
                    }`}
                  >
                    {REJECT_TAG_LABELS[tag]}
                  </button>
                );
              })}
            </div>
          </div>

          <label className="block text-xs">
            <span className="mb-1 block font-medium text-slate-700">备注</span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              placeholder="例如：不要在 scope 未确认前谈价"
              className="w-full rounded border border-slate-200 px-2 py-1.5 text-xs text-slate-800"
            />
          </label>

          <label className="block text-xs">
            <span className="mb-1 block font-medium text-slate-700">建议改法</span>
            <textarea
              value={suggestedFix}
              onChange={(e) => setSuggestedFix(e.target.value)}
              rows={2}
              placeholder="例如：先问对方倾向的 deliverable 形式"
              className="w-full rounded border border-slate-200 px-2 py-1.5 text-xs text-slate-800"
            />
          </label>
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-100 px-4 py-3">
          <button
            type="button"
            onClick={() => {
              reset();
              onClose();
            }}
            disabled={loading}
            className="rounded border border-slate-200 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
          >
            取消
          </button>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={() => void handleSubmit()}
            className="rounded bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-700 disabled:opacity-50"
          >
            {loading ? '提交中…' : '确认驳回'}
          </button>
        </div>
      </div>
    </div>
  );
}
