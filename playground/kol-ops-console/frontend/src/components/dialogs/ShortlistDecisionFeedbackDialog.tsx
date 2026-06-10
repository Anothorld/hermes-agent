import { useEffect, useMemo, useState } from 'react';
import { api } from '../../api';
import { errorSummary } from '../../lib/errors';

export type DecisionAction = 'approve' | 'remove' | 'transfer';

export type DecisionTag = {
  tag: string;
  label_zh: string;
  action_scope: string;
  status: string;
  source: string;
};

export type DecisionFeedback = {
  shared_tags: string[];
  shared_comment: string | null;
  per_kol_overrides: Record<string, { tags: string[]; comment: string | null }>;
};

export const EMPTY_DECISION_FEEDBACK: DecisionFeedback = {
  shared_tags: [],
  shared_comment: null,
  per_kol_overrides: {},
};

type FeedbackRequirements = {
  sku?: string | null;
  category?: string | null;
  sku_sample_count?: number;
  comment_required_threshold?: number;
  comment_required?: boolean;
  /** Console kill switch KOC_DISCOVERY_FEEDBACK_REQUIRED — when false the
   *  backend skips validation entirely and the dialog must not block. */
  feedback_required?: boolean;
  degraded?: boolean;
};

const COMMENT_PLACEHOLDER =
  '用一句话说出真实想法，例如：粉丝画像太低龄化了，我们要的是精致白领。';

/** Fetch the active tag vocabulary for one decision action. */
export function useDecisionTags(action: DecisionAction, enabled: boolean) {
  const [tags, setTags] = useState<DecisionTag[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    api
      .get<{ tags: DecisionTag[] }>(`/learning/discovery-tags?action=${action}`)
      .then((r) => {
        if (!cancelled) setTags(r.tags ?? []);
      })
      .catch((ex) => {
        if (!cancelled) setError(errorSummary(ex));
      });
    return () => {
      cancelled = true;
    };
  }, [action, enabled]);
  return { tags, error };
}

/** Fetch whether the free-text comment is still required for this SPU. */
export function useFeedbackRequirements(
  sku: string | null | undefined,
  env: string,
  enabled: boolean,
) {
  const [req, setReq] = useState<FeedbackRequirements | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const qs = new URLSearchParams({ env });
    if (sku) qs.set('sku', sku);
    api
      .get<FeedbackRequirements>(`/learning/discovery-feedback-requirements?${qs.toString()}`)
      .then((r) => {
        if (!cancelled) setReq(r);
      })
      .catch(() => {
        // Degrade: match backend leniency when bridge is unreachable.
        if (!cancelled) {
          setReq({ comment_required: false, feedback_required: false, degraded: true });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sku, env, enabled]);
  return req;
}

/** Reusable tag checklist (also embedded in the transfer dialog). */
export function DecisionTagChecklist({
  tags,
  selected,
  onToggle,
  idPrefix,
}: {
  tags: DecisionTag[];
  selected: string[];
  onToggle: (tag: string, checked: boolean) => void;
  idPrefix: string;
}) {
  if (tags.length === 0) {
    return <div className="text-[11px] text-slate-400">标签加载中…</div>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {tags.map((t) => {
        const checked = selected.includes(t.tag);
        return (
          <label
            key={t.tag}
            htmlFor={`${idPrefix}-${t.tag}`}
            className={`cursor-pointer select-none rounded-full border px-2 py-1 text-[11px] ${
              checked
                ? 'border-emerald-500 bg-emerald-50 text-emerald-900'
                : 'border-slate-300 bg-white text-slate-600 hover:bg-slate-50'
            }`}
          >
            <input
              id={`${idPrefix}-${t.tag}`}
              type="checkbox"
              className="mr-1 align-middle"
              checked={checked}
              onChange={(e) => onToggle(t.tag, e.target.checked)}
            />
            {t.label_zh}
          </label>
        );
      })}
    </div>
  );
}

type KolRow = { handle: string; displayName?: string | null };

type Props = {
  open: boolean;
  action: Exclude<DecisionAction, 'transfer'>;
  kols: KolRow[];
  sku?: string | null;
  env: string;
  title: string;
  description?: string;
  confirmLabel: string;
  /** Row-level cached approve annotations (shown read-only in batch approve). */
  perKolFeedback?: Record<string, { tags: string[]; comment: string | null }>;
  onClose: () => void;
  onSubmit: (feedback: DecisionFeedback) => Promise<void>;
};

/**
 * Operator feedback dialog for shortlist decisions (approve / remove).
 *
 * Batch approve shares one set of tags + comment across all selected KOLs;
 * per-KOL detail is captured on each list row (thumbs-up) and shown here
 * read-only before submit. Tags are always required when feedback is on;
 * the comment is required while the SPU is in the early-learning phase.
 */
export function ShortlistDecisionFeedbackDialog({
  open,
  action,
  kols,
  sku,
  env,
  title,
  description,
  confirmLabel,
  perKolFeedback = {},
  onClose,
  onSubmit,
}: Props) {
  const { tags, error: tagErr } = useDecisionTags(action, open);
  const req = useFeedbackRequirements(sku, env, open);
  const [sharedTags, setSharedTags] = useState<string[]>([]);
  const [sharedComment, setSharedComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [validationErr, setValidationErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setSharedTags([]);
    setSharedComment('');
    setSubmitting(false);
    setValidationErr(null);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, submitting, onClose]);

  // Kill switch or bridge outage: never block the operator in the dialog.
  const feedbackRequired =
    !req?.degraded && req?.feedback_required !== false;
  const commentRequired = feedbackRequired && req?.comment_required !== false;
  const sampleCount = req?.sku_sample_count ?? 0;
  const threshold = req?.comment_required_threshold ?? 0;

  const effective = useMemo(() => {
    const perKol: Record<string, { tags: string[]; comment: string | null }> = {};
    for (const k of kols) {
      const cached = perKolFeedback[k.handle];
      if (cached && (cached.tags.length > 0 || cached.comment)) {
        perKol[k.handle] = {
          tags: cached.tags.length > 0 ? cached.tags : sharedTags,
          comment: cached.comment ?? (sharedComment.trim() || null),
        };
      }
    }
    return {
      shared_tags: sharedTags,
      shared_comment: sharedComment.trim() || null,
      per_kol_overrides: perKol,
    } satisfies DecisionFeedback;
  }, [kols, perKolFeedback, sharedTags, sharedComment]);

  if (!open) return null;

  const validate = (): string | null => {
    if (!feedbackRequired) return null;
    for (const k of kols) {
      const cached = perKolFeedback[k.handle];
      const tagsFor =
        cached?.tags?.length ? cached.tags : sharedTags;
      if (tagsFor.length === 0) {
        return cached
          ? `请为 @${k.handle} 的标注至少勾选一个原因标签`
          : `请为 @${k.handle} 至少勾选一个原因标签（或在列表行上单独标注）`;
      }
      if (commentRequired) {
        const commentFor =
          cached?.comment?.trim()
            ? cached.comment.trim()
            : sharedComment.trim();
        if (!commentFor) {
          return `请为 @${k.handle} 写一句真实理由（共享评论或列表行标注）`;
        }
      }
    }
    return null;
  };

  const submit = async () => {
    const err = validate();
    if (err) {
      setValidationErr(err);
      return;
    }
    setValidationErr(null);
    setSubmitting(true);
    try {
      await onSubmit(effective);
      onClose();
    } catch (ex) {
      setValidationErr(errorSummary(ex));
      setSubmitting(false);
    }
  };

  const annotatedCount = kols.filter(
    (k) =>
      (perKolFeedback[k.handle]?.tags?.length ?? 0) > 0
      || !!(perKolFeedback[k.handle]?.comment ?? '').trim(),
  ).length;

  const tagLabel = (slug: string) =>
    tags.find((t) => t.tag === slug)?.label_zh ?? slug;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="decision-feedback-title"
    >
      <div className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-lg border border-slate-200 bg-white p-4 shadow-lg">
        <h2 id="decision-feedback-title" className="text-sm font-semibold text-slate-900">
          {title}
        </h2>
        {description && <p className="mt-1 text-xs text-slate-600">{description}</p>}
        <p className="mt-1 text-[11px] text-slate-500">
          您的标签和评论会成为 AI 的学习样本，帮助下一轮发现更符合预期的 KOL。
        </p>
        {commentRequired && (
          <div className="mt-2 rounded border border-sky-200 bg-sky-50 px-2 py-1 text-[11px] text-sky-900">
            学习初期需要您说明真实理由
            {threshold > 0 && (
              <>
                （该产品已积累 {sampleCount}/{threshold} 条样本，达到后评论改为选填）
              </>
            )}
          </div>
        )}

        <div className="mt-3 space-y-3 text-xs">
          <div>
            <div className="mb-1 font-medium text-slate-700">
              原因标签{kols.length > 1 ? '（应用于本批全部 KOL）' : ''}
              {feedbackRequired ? (
                <span className="ml-1 font-normal text-rose-600">*必选</span>
              ) : (
                <span className="ml-1 font-normal text-slate-400">（当前为选填）</span>
              )}
            </div>
            {tagErr ? (
              <div className="text-rose-700">{tagErr}</div>
            ) : (
              <DecisionTagChecklist
                tags={tags}
                selected={sharedTags}
                idPrefix="shared"
                onToggle={(tag, checked) =>
                  setSharedTags((prev) =>
                    checked ? [...prev, tag] : prev.filter((t) => t !== tag),
                  )
                }
              />
            )}
          </div>

          <div>
            <label className="mb-1 block font-medium text-slate-700" htmlFor="shared-comment">
              评论{kols.length > 1 ? '（应用于本批全部 KOL）' : ''}
              {commentRequired ? (
                <span className="ml-1 font-normal text-rose-600">*必填</span>
              ) : (
                <span className="ml-1 font-normal text-slate-400">（选填，但越多越好）</span>
              )}
            </label>
            <textarea
              id="shared-comment"
              value={sharedComment}
              onChange={(e) => setSharedComment(e.target.value)}
              rows={3}
              maxLength={2000}
              placeholder={COMMENT_PLACEHOLDER}
              className="w-full rounded border border-slate-300 px-2 py-1.5"
            />
          </div>

          {action === 'approve' && kols.length > 0 && (
            <div>
              <div className="mb-1 font-medium text-slate-700">
                本批 KOL 标注汇总（{kols.length} 个
                {annotatedCount > 0 && ` · ${annotatedCount} 个已单独标注`}）
              </div>
              <p className="mb-1.5 text-[11px] text-slate-500">
                未单独标注的 KOL 将使用上方共享标签与评论。可在列表行点击
                <span className="mx-0.5 font-medium text-emerald-700">点赞</span>
                按钮补充或修改。
              </p>
              <ul className="max-h-48 space-y-1 overflow-y-auto">
                {kols.map((k) => {
                  const cached = perKolFeedback[k.handle];
                  const hasOwn =
                    (cached?.tags?.length ?? 0) > 0
                    || !!(cached?.comment ?? '').trim();
                  return (
                    <li
                      key={k.handle}
                      className={`rounded border px-2 py-1.5 ${
                        hasOwn
                          ? 'border-emerald-200 bg-emerald-50/60'
                          : 'border-slate-200 bg-slate-50/80'
                      }`}
                    >
                      <div className="font-medium text-slate-800">@{k.handle}</div>
                      {hasOwn ? (
                        <div className="mt-0.5 text-[11px] text-slate-600">
                          {(cached?.tags ?? []).length > 0 && (
                            <span>
                              {(cached?.tags ?? []).map(tagLabel).join('、')}
                            </span>
                          )}
                          {cached?.comment && (
                            <span className="mt-0.5 block text-slate-500">
                              「{cached.comment.length > 80
                                ? `${cached.comment.slice(0, 80)}…`
                                : cached.comment}」
                            </span>
                          )}
                        </div>
                      ) : (
                        <div className="mt-0.5 text-[11px] italic text-slate-400">
                          将使用本批共享理由
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>

        {validationErr && (
          <div className="mt-3 rounded border border-rose-300 bg-rose-50 px-2 py-1 text-[11px] text-rose-800">
            {validationErr}
          </div>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            disabled={submitting}
            onClick={onClose}
            className="rounded border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            disabled={submitting}
            onClick={() => void submit()}
            className={`rounded px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50 ${
              action === 'remove'
                ? 'bg-rose-600 hover:bg-rose-700'
                : 'bg-emerald-600 hover:bg-emerald-700'
            }`}
          >
            {submitting ? '提交中…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
