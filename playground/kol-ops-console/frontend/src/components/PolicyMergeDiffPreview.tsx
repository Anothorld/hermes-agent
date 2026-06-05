import { useEffect, useState } from 'react';
import { api } from '../api';
import { policyScopeLabel } from '../constants/domainLabels';

type MergeSection = {
  scope: string;
  current_md: string;
  merged_md: string;
  merge_mode_used?: string;
  merge_effect?: 'replace' | 'add_new' | 'append_delta';
  delta_chars?: number;
};

const MERGE_EFFECT_LABEL: Record<string, string> = {
  replace: '将替换',
  add_new: '将新增',
  append_delta: '将追加',
};

const MERGE_EFFECT_CLASS: Record<string, string> = {
  replace: 'bg-amber-100 text-amber-900',
  add_new: 'bg-emerald-100 text-emerald-900',
  append_delta: 'bg-sky-100 text-sky-900',
};

type MergePreview = {
  merge_mode?: string;
  sections?: Record<string, MergeSection>;
};

/** Shows current vs post-approval merged policy (Bridge deterministic preview). */
export function PolicyMergeDiffPreview({
  env,
  proposal,
}: {
  env: string;
  proposal: Record<string, unknown>;
}) {
  const [preview, setPreview] = useState<MergePreview | null>(null);
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .post<MergePreview>('/learning/policy-merge-preview', { env, proposal })
      .then((r) => {
        if (alive) {
          setPreview(r);
          setErr(null);
        }
      })
      .catch(() => {
        if (alive) setErr('无法加载合并预览');
      });
    return () => {
      alive = false;
    };
  }, [env, proposal]);

  const sections = preview?.sections ?? {};
  const keys = Object.keys(sections);
  if (!keys.length && !err) return null;

  return (
    <div className="rounded border border-sky-200 bg-sky-50/40 p-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-[11px] font-medium text-sky-800 hover:text-sky-950"
      >
        {open ? '收起' : '查看'}批准后 policy 合并效果（与当前对比）
        {preview?.merge_mode ? ` · 模式 ${preview.merge_mode}` : ''}
      </button>
      {err && <p className="mt-1 text-[11px] text-rose-700">{err}</p>}
      {open && keys.length > 0 && (
        <div className="mt-2 space-y-2">
          {keys.map((k) => {
            const s = sections[k];
            const delta = s.delta_chars ?? 0;
            return (
              <div key={k} className="space-y-1">
                <div className="flex flex-wrap items-center gap-1 text-[11px] font-medium text-slate-700">
                  {policyScopeLabel(s.scope)}
                  {s.merge_effect && MERGE_EFFECT_LABEL[s.merge_effect] && (
                    <span
                      className={
                        'rounded px-1.5 py-0.5 text-[10px] font-medium ' +
                        (MERGE_EFFECT_CLASS[s.merge_effect] ?? 'bg-slate-100')
                      }
                    >
                      {MERGE_EFFECT_LABEL[s.merge_effect]}
                    </span>
                  )}
                  {delta !== 0 && (
                    <span className="text-slate-500">
                      ({delta > 0 ? '+' : ''}
                      {delta} 字符)
                    </span>
                  )}
                </div>
                <div className="grid gap-2 md:grid-cols-2">
                  <div>
                    <div className="text-[10px] text-slate-500">当前</div>
                    <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded border border-slate-200 bg-white p-1.5 text-[10px]">
                      {s.current_md.trim() || '(空)'}
                    </pre>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-500">批准后合并结果</div>
                    <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded border border-emerald-200 bg-emerald-50/50 p-1.5 text-[10px]">
                      {s.merged_md.trim()}
                    </pre>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
