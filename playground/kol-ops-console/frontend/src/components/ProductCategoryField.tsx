import { useEffect, useState } from 'react';
import { api } from '../api';
import { errorSummary } from '../lib/errors';
import { toast } from '../lib/store';

type CategoryRow = {
  sku: string;
  category: string;
  source: 'llm' | 'operator' | string;
  confidence?: number | null;
  updated_at?: string;
};

/**
 * Editable「品类」field on the product page.
 *
 * The category groups learning samples across products of the same kind so
 * the discovery criteria can generalize beyond a single SPU. AI suggests a
 * category nightly; an operator edit here is authoritative and will not be
 * overwritten.
 */
export function ProductCategoryField({
  sku,
  productName,
}: {
  sku: string;
  productName?: string | null;
}) {
  const [row, setRow] = useState<CategoryRow | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<{ categories: CategoryRow[] }>('/learning/product-categories')
      .then((r) => {
        if (cancelled) return;
        setRow((r.categories ?? []).find((c) => c.sku === sku) ?? null);
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
    return () => {
      cancelled = true;
    };
  }, [sku]);

  const save = async () => {
    const category = draft.trim();
    if (!category) {
      toast.error('请填写品类', '例如：ergonomic_chair / 人体工学椅');
      return;
    }
    setSaving(true);
    try {
      await api.put(`/learning/product-categories/${encodeURIComponent(sku)}`, {
        category,
        product_name: productName ?? undefined,
      });
      setRow({ sku, category, source: 'operator' });
      setEditing(false);
      toast.success('品类已更新', `${sku} → ${category}`);
    } catch (ex) {
      toast.error('品类更新失败', errorSummary(ex));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div className="font-medium text-slate-500">
        品类
        <span className="ml-1 font-normal text-slate-400">
          — 用于跨产品归纳 KOL 评判标准
        </span>
      </div>
      {!loaded ? (
        <div className="text-slate-400">加载中…</div>
      ) : editing ? (
        <div className="mt-1 flex items-center gap-1">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={80}
            placeholder="例如：ergonomic_chair"
            className="w-48 rounded border border-slate-300 px-2 py-1"
          />
          <button
            type="button"
            disabled={saving}
            onClick={() => void save()}
            className="rounded bg-emerald-600 px-2 py-1 text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {saving ? '保存中…' : '保存'}
          </button>
          <button
            type="button"
            disabled={saving}
            onClick={() => setEditing(false)}
            className="rounded border border-slate-300 bg-white px-2 py-1 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          >
            取消
          </button>
        </div>
      ) : (
        <div className="mt-1 flex items-center gap-2 text-slate-700">
          {row ? (
            <>
              <span className="rounded bg-indigo-50 px-2 py-0.5 font-mono text-indigo-800">
                {row.category}
              </span>
              <span className="text-[10px] text-slate-400">
                {row.source === 'operator' ? '人工设置' : 'AI 建议（可修改）'}
              </span>
            </>
          ) : (
            <span className="italic text-slate-400">
              暂未归类 — AI 会在夜间学习时自动建议，也可手动设置
            </span>
          )}
          <button
            type="button"
            onClick={() => {
              setDraft(row?.category ?? '');
              setEditing(true);
            }}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-slate-600 hover:bg-slate-50"
          >
            {row ? '修改' : '设置品类'}
          </button>
        </div>
      )}
    </div>
  );
}
