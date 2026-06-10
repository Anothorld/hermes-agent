import { useEffect, useState } from 'react';
import {
  DecisionTagChecklist,
  useDecisionTags,
  useFeedbackRequirements,
  type DecisionFeedback,
} from './ShortlistDecisionFeedbackDialog';

export type KolFeedbackEntry = {
  tags: string[];
  comment: string | null;
};

function ThumbsUpIcon({ filled }: { filled?: boolean }) {
  return (
    <svg
      viewBox="0 0 20 20"
      className="inline h-3.5 w-3.5 shrink-0"
      fill="currentColor"
      aria-hidden="true"
    >
      <path
        fillRule="evenodd"
        d="M2 10.5a1.5 1.5 0 1 1 3 0v6a1.5 1.5 0 0 1-3 0v-6ZM6 10.333v5.43a2 2 0 0 0 1.106 1.79l.05.025A4 4 0 0 0 12.8 16H14a2 2 0 0 0 2-2v-1.382a1 1 0 0 0-.553-.894l-1.39-.695A2 2 0 0 1 13 9.382V7.723a2 2 0 0 1 .447-1.342l.05-.066a2 2 0 0 0 .106-2.183 2 2 0 0 0-1.648-1.125l-1.728-.012a2 2 0 0 0-1.902 1.12l-1.204 2.408A1 1 0 0 1 6 10.333Z"
        clipRule="evenodd"
        opacity={filled ? 1 : 0.55}
      />
    </svg>
  );
}

export { ThumbsUpIcon };

type Props = {
  open: boolean;
  handle: string;
  displayName?: string | null;
  sku?: string | null;
  env: string;
  initial?: KolFeedbackEntry | null;
  onClose: () => void;
  onSave: (entry: KolFeedbackEntry) => void;
};

const COMMENT_PLACEHOLDER =
  '用一句话说出真实想法，例如：粉丝画像符合精致白领，内容调性高级。';

/**
 * Single-KOL approve annotation — tags + comment cached locally until batch
 * approve confirms in the secondary dialog.
 */
export function KolApproveAnnotationDialog({
  open,
  handle,
  displayName,
  sku,
  env,
  initial,
  onClose,
  onSave,
}: Props) {
  const { tags, error: tagErr } = useDecisionTags('approve', open);
  const req = useFeedbackRequirements(sku, env, open);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [comment, setComment] = useState('');
  const [validationErr, setValidationErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setSelectedTags(initial?.tags ?? []);
    setComment(initial?.comment ?? '');
    setValidationErr(null);
  }, [open, initial]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const feedbackRequired = !req?.degraded && req?.feedback_required !== false;
  const commentRequired = feedbackRequired && req?.comment_required !== false;

  if (!open) return null;

  const validate = (): string | null => {
    if (!feedbackRequired) return null;
    if (selectedTags.length === 0) {
      return '请至少勾选一个原因标签';
    }
    if (commentRequired && !comment.trim()) {
      return '学习初期需要您写一句真实理由（评论必填）';
    }
    return null;
  };

  const save = () => {
    const err = validate();
    if (err) {
      setValidationErr(err);
      return;
    }
    onSave({
      tags: selectedTags,
      comment: comment.trim() || null,
    });
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="kol-approve-annotate-title"
    >
      <div className="max-h-[85vh] w-full max-w-md overflow-y-auto rounded-lg border border-slate-200 bg-white p-4 shadow-lg">
        <h2 id="kol-approve-annotate-title" className="text-sm font-semibold text-slate-900">
          标注 @{handle}
          {displayName ? `（${displayName}）` : ''}
        </h2>
        <p className="mt-1 text-xs text-slate-600">
          填写这位 KOL 的批准理由。保存后暂存于本页，点击底部「Approve」时一并提交。
        </p>
        {commentRequired && (
          <div className="mt-2 rounded border border-sky-200 bg-sky-50 px-2 py-1 text-[11px] text-sky-900">
            学习初期需要说明真实理由（该产品样本未达阈值前评论必填）。
          </div>
        )}

        <div className="mt-3 space-y-3 text-xs">
          <div>
            <div className="mb-1 font-medium text-slate-700">
              原因标签
              {feedbackRequired ? (
                <span className="ml-1 font-normal text-rose-600">*必选</span>
              ) : (
                <span className="ml-1 font-normal text-slate-400">（选填）</span>
              )}
            </div>
            {tagErr ? (
              <div className="text-rose-700">{tagErr}</div>
            ) : (
              <DecisionTagChecklist
                tags={tags}
                selected={selectedTags}
                idPrefix={`annotate-${handle}`}
                onToggle={(tag, checked) =>
                  setSelectedTags((prev) =>
                    checked ? [...prev, tag] : prev.filter((t) => t !== tag),
                  )
                }
              />
            )}
          </div>
          <div>
            <label className="mb-1 block font-medium text-slate-700" htmlFor="annotate-comment">
              评论
              {commentRequired ? (
                <span className="ml-1 font-normal text-rose-600">*必填</span>
              ) : (
                <span className="ml-1 font-normal text-slate-400">（选填，但越多越好）</span>
              )}
            </label>
            <textarea
              id="annotate-comment"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={3}
              maxLength={2000}
              placeholder={COMMENT_PLACEHOLDER}
              className="w-full rounded border border-slate-300 px-2 py-1.5"
            />
          </div>
        </div>

        {validationErr && (
          <div className="mt-3 rounded border border-rose-300 bg-rose-50 px-2 py-1 text-[11px] text-rose-800">
            {validationErr}
          </div>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
          >
            取消
          </button>
          <button
            type="button"
            onClick={save}
            className="inline-flex items-center gap-1 rounded bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700"
          >
            <ThumbsUpIcon filled />
            保存标注
          </button>
        </div>
      </div>
    </div>
  );
}

/** Merge row-level cached annotations into the approve batch payload. */
export function mergeApproveFeedback(
  selectedHandles: string[],
  shared: Pick<DecisionFeedback, 'shared_tags' | 'shared_comment'>,
  perKolCache: Record<string, KolFeedbackEntry>,
): DecisionFeedback {
  const per_kol_overrides: DecisionFeedback['per_kol_overrides'] = {};
  for (const handle of selectedHandles) {
    const cached = perKolCache[handle];
    if (!cached || (cached.tags.length === 0 && !cached.comment)) continue;
    per_kol_overrides[handle] = {
      tags: cached.tags.length > 0 ? cached.tags : shared.shared_tags,
      comment: cached.comment ?? shared.shared_comment,
    };
  }
  return {
    shared_tags: shared.shared_tags,
    shared_comment: shared.shared_comment,
    per_kol_overrides,
  };
}
