import { FormEvent, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';

type ProductVariant = {
  id: string;
  label?: string | null;
  url?: string | null;
  attributes?: Record<string, string>;
};

type ProductSummary = {
  sku: string;
  name: string;
  url: string | null;
  tags: string[];
  notes: string | null;
  pitch_md: string | null;
  selling_points: string | null;
  variants: ProductVariant[];
  variant_count: number;
  default_budget_per_kol: number | null;
  default_budget_total: number | null;
  default_absolute_floor: number | null;
  campaigns_total: number;
  campaigns_running: number;
  active_campaign_ids: string[];
  stage: string | null;
  sub_status: string | null;
  last_event_type: string | null;
  last_event_ts: string | null;
  kols_contacted: number;
};

function StatusBadges({ p }: { p: ProductSummary }) {
  if (p.campaigns_total === 0) {
    return <span className="ml-2 rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500">idle</span>;
  }
  return (
    <span className="ml-2 inline-flex flex-wrap items-center gap-1 text-xs">
      {p.campaigns_running > 0 ? (
        <span className="rounded bg-emerald-100 px-2 py-0.5 text-emerald-800">
          running × {p.campaigns_running}
        </span>
      ) : (
        <span className="rounded bg-slate-200 px-2 py-0.5 text-slate-700">no active run</span>
      )}
      {p.stage && (
        <span className="rounded bg-sky-100 px-2 py-0.5 text-sky-800">stage: {p.stage}</span>
      )}
      {p.kols_contacted > 0 && (
        <span className="rounded bg-violet-100 px-2 py-0.5 text-violet-800">
          KOLs · {p.kols_contacted}
        </span>
      )}
      {p.last_event_ts && (
        <span className="text-slate-400">last: {p.last_event_ts.replace('T', ' ').slice(0, 19)}</span>
      )}
    </span>
  );
}

type DraftForm = {
  sku: string;
  name: string;
  url: string;
  tags: string;
  notes: string;
  selling_points: string;
  pitch_md: string;
  default_budget_per_kol: string;
  default_budget_total: string;
  default_absolute_floor: string;
};

const EMPTY_DRAFT: DraftForm = {
  sku: '',
  name: '',
  url: '',
  tags: '',
  notes: '',
  selling_points: '',
  pitch_md: '',
  default_budget_per_kol: '',
  default_budget_total: '',
  default_absolute_floor: '',
};

export function ProductListPage() {
  const [items, setItems] = useState<ProductSummary[]>([]);
  const [draft, setDraft] = useState<DraftForm>(EMPTY_DRAFT);
  const [variants, setVariants] = useState<ProductVariant[]>([]);
  const [variantBusy, setVariantBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const refresh = () =>
    api
      .get<ProductSummary[]>('/products/summary')
      .then(setItems)
      .catch((e) => setErr(String(e)));

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15_000);
    return () => clearInterval(t);
  }, []);

  const onDetectVariants = async () => {
    const u = draft.url.trim();
    if (!u) {
      setErr('请先填写商品链接，再点 Detect variants');
      return;
    }
    setVariantBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const r = await api.post<{ variants: ProductVariant[] }>('/products/parse-variants', { url: u });
      if (!r.variants || r.variants.length === 0) {
        setMsg('链接里没有识别出 variant 参数，请手动添加 variant。');
      } else {
        // Merge: dedup by id; new variants append.
        setVariants((prev) => {
          const ids = new Set(prev.map((v) => v.id));
          const merged = [...prev];
          for (const v of r.variants) {
            if (!ids.has(v.id)) merged.push(v);
          }
          return merged;
        });
        setMsg(`已从链接解析出 ${r.variants.length} 个 variant`);
      }
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setVariantBusy(false);
    }
  };

  const onAddVariantRow = () => {
    setVariants((prev) => [...prev, { id: '', label: '', url: '', attributes: {} }]);
  };

  const onUpdateVariant = (idx: number, patch: Partial<ProductVariant>) => {
    setVariants((prev) => prev.map((v, i) => (i === idx ? { ...v, ...patch } : v)));
  };

  const onRemoveVariant = (idx: number) => {
    setVariants((prev) => prev.filter((_, i) => i !== idx));
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    const cleanVariants = variants
      .map((v) => ({
        id: v.id.trim(),
        label: (v.label || '').trim() || null,
        url: (v.url || '').trim() || null,
        attributes: v.attributes || {},
      }))
      .filter((v) => v.id.length > 0);
    const seen = new Set<string>();
    for (const v of cleanVariants) {
      if (seen.has(v.id)) {
        setErr(`variant id "${v.id}" 重复，请去重`);
        return;
      }
      seen.add(v.id);
    }
    const parseNum = (s: string): number | null => {
      const t = s.trim();
      if (!t) return null;
      const n = Number(t);
      return Number.isFinite(n) ? n : null;
    };
    try {
      await api.post('/products', {
        sku: draft.sku.trim(),
        name: draft.name.trim(),
        url: draft.url.trim() || null,
        tags: draft.tags.split(',').map((t) => t.trim()).filter(Boolean),
        notes: draft.notes || null,
        pitch_md: draft.pitch_md.trim() || null,
        selling_points: draft.selling_points.trim() || null,
        variants: cleanVariants,
        default_budget_per_kol: parseNum(draft.default_budget_per_kol),
        default_budget_total: parseNum(draft.default_budget_total),
        default_absolute_floor: parseNum(draft.default_absolute_floor),
      });
      setDraft(EMPTY_DRAFT);
      setVariants([]);
      setMsg(`Saved product ${draft.sku}`);
      refresh();
    } catch (ex) {
      setErr(String(ex));
    }
  };

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Products (SKU catalog)</h1>
      <form onSubmit={submit} className="space-y-3 rounded border bg-white p-3 text-sm">
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          <label className="flex flex-col text-xs">
            <span className="text-slate-500">SKU *</span>
            <input
              placeholder="POVISON-TS-8319"
              value={draft.sku}
              onChange={(e) => setDraft({ ...draft, sku: e.target.value })}
              className="rounded border px-2 py-1"
              required
            />
          </label>
          <label className="flex flex-col text-xs md:col-span-2">
            <span className="text-slate-500">Name *</span>
            <input
              placeholder="78.74 Modern Minimalist TV Stand"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              className="rounded border px-2 py-1"
              required
            />
          </label>
        </div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          <label className="flex flex-col text-xs md:col-span-2">
            <span className="text-slate-500">
              Product URL{' '}
              <span className="text-slate-400">(支持 ?variant=... 解析)</span>
            </span>
            <input
              placeholder="https://www.povison.com/...?variant=32529"
              value={draft.url}
              onChange={(e) => setDraft({ ...draft, url: e.target.value })}
              className="rounded border px-2 py-1"
            />
          </label>
          <label className="flex flex-col text-xs">
            <span className="text-slate-500">Tags (逗号分隔)</span>
            <input
              placeholder="furniture, tv-stand, walnut"
              value={draft.tags}
              onChange={(e) => setDraft({ ...draft, tags: e.target.value })}
              className="rounded border px-2 py-1"
            />
          </label>
        </div>
        <label className="flex flex-col text-xs">
          <span className="text-slate-500">
            Selling points <span className="text-amber-600">*</span>{' '}
            <span className="text-slate-400">(产品卖点 / 受众 / 类别，KOL discovery 会读这些)</span>
          </span>
          <textarea
            value={draft.selling_points}
            onChange={(e) => setDraft({ ...draft, selling_points: e.target.value })}
            rows={3}
            className="rounded border px-2 py-1"
            placeholder="例如：实木桃花心、60” 长 4-tier、适合美式 / 中古博主"
          />
        </label>
        <label className="flex flex-col text-xs">
          <span className="text-slate-500">
            Pitch (markdown) <span className="text-slate-400">(可选；如果有更详细的卖点文档贴这里)</span>
          </span>
          <textarea
            value={draft.pitch_md}
            onChange={(e) => setDraft({ ...draft, pitch_md: e.target.value })}
            rows={4}
            className="rounded border px-2 py-1 font-mono"
            placeholder={'# POVISON TS-8319 桃花心电视柜\n- 卖点 1: ...\n- 卖点 2: ...\n- 受众: ...'}
          />
        </label>

        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          <label className="flex flex-col text-xs">
            <span className="text-slate-500">默认 paid_ceiling / KOL (USD)</span>
            <input
              type="number"
              min="0"
              step="0.01"
              value={draft.default_budget_per_kol}
              onChange={(e) => setDraft({ ...draft, default_budget_per_kol: e.target.value })}
              placeholder="500"
              className="rounded border px-2 py-1"
            />
          </label>
          <label className="flex flex-col text-xs">
            <span className="text-slate-500">默认 total budget（仅写入 brief, USD）</span>
            <input
              type="number"
              min="0"
              step="0.01"
              value={draft.default_budget_total}
              onChange={(e) => setDraft({ ...draft, default_budget_total: e.target.value })}
              placeholder="12000"
              className="rounded border px-2 py-1"
            />
          </label>
          <label className="flex flex-col text-xs">
            <span className="text-slate-500">默认 absolute_floor (USD)</span>
            <input
              type="number"
              min="0"
              step="0.01"
              value={draft.default_absolute_floor}
              onChange={(e) => setDraft({ ...draft, default_absolute_floor: e.target.value })}
              placeholder="1000"
              className="rounded border px-2 py-1"
            />
          </label>
        </div>

        <div className="rounded border border-slate-200 p-2">
          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium text-slate-700">Variants (颜色 / 尺寸 / 规格)</span>
            <span className="text-slate-400">
              用于后续 KOL 选品 & 合同 PRODUCT_SPECS。如果商品没有规格选项可以留空。
            </span>
            <button
              type="button"
              onClick={onDetectVariants}
              disabled={variantBusy}
              className="ml-auto rounded border border-sky-300 px-2 py-0.5 text-sky-700 hover:bg-sky-50 disabled:opacity-50"
            >
              {variantBusy ? '解析中…' : 'Detect variants from URL'}
            </button>
            <button
              type="button"
              onClick={onAddVariantRow}
              className="rounded border border-emerald-300 px-2 py-0.5 text-emerald-700 hover:bg-emerald-50"
            >
              + Add variant
            </button>
          </div>
          {variants.length === 0 ? (
            <div className="text-xs italic text-slate-400">No variants yet.</div>
          ) : (
            <ul className="space-y-1">
              {variants.map((v, idx) => (
                <li key={idx} className="grid grid-cols-12 items-center gap-1 text-xs">
                  <input
                    placeholder="variant id"
                    value={v.id}
                    onChange={(e) => onUpdateVariant(idx, { id: e.target.value })}
                    className="col-span-2 rounded border px-1 py-0.5 font-mono"
                    required
                  />
                  <input
                    placeholder="label / 颜色 / 尺寸"
                    value={v.label || ''}
                    onChange={(e) => onUpdateVariant(idx, { label: e.target.value })}
                    className="col-span-3 rounded border px-1 py-0.5"
                  />
                  <input
                    placeholder="variant url (可选)"
                    value={v.url || ''}
                    onChange={(e) => onUpdateVariant(idx, { url: e.target.value })}
                    className="col-span-6 rounded border px-1 py-0.5"
                  />
                  <button
                    type="button"
                    onClick={() => onRemoveVariant(idx)}
                    className="col-span-1 rounded border border-rose-300 px-1 py-0.5 text-rose-700 hover:bg-rose-50"
                    title="Remove this variant"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <label className="flex flex-col text-xs">
          <span className="text-slate-500">Notes (其他备注，可选)</span>
          <textarea
            value={draft.notes}
            onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
            rows={2}
            className="rounded border px-2 py-1"
          />
        </label>

        <div className="flex justify-end">
          <button className="rounded bg-emerald-600 px-3 py-1 text-white">Save product</button>
        </div>
      </form>
      {err && <div className="text-sm text-red-600">{err}</div>}
      {msg && <div className="text-sm text-emerald-700">{msg}</div>}
      <ul className="divide-y rounded border bg-white">
        {items.map((p) => (
          <li key={p.sku} className="px-3 py-2">
            <div className="flex flex-wrap items-center">
              <Link
                to={`/products/${encodeURIComponent(p.sku)}`}
                className="font-medium hover:text-emerald-700"
              >
                {p.sku} — {p.name}
              </Link>
              {p.tags.length > 0 && (
                <span className="ml-2 text-xs text-slate-500">[{p.tags.join(', ')}]</span>
              )}
              {p.variant_count > 0 && (
                <span className="ml-2 rounded bg-indigo-100 px-2 py-0.5 text-xs text-indigo-800">
                  {p.variant_count} variant{p.variant_count === 1 ? '' : 's'}
                </span>
              )}
              <StatusBadges p={p} />
            </div>
            {p.active_campaign_ids.length > 0 && (
              <div className="mt-1 text-xs text-slate-500">
                active campaigns: {p.active_campaign_ids.join(', ')}
              </div>
            )}
          </li>
        ))}
        {items.length === 0 && (
          <li className="px-3 py-4 text-sm text-slate-500">No products yet.</li>
        )}
      </ul>
    </div>
  );
}
