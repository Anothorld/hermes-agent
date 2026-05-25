import { FormEvent, useState } from 'react';
import { api } from '../api';

type Props = {
  campaignId: string;
  env: string;
  onSaved?: () => void;
};

const PLATFORM_CHOICES = ['instagram', 'tiktok', 'youtube', 'twitter', 'blog'] as const;

// Lightweight inline form for patching the bits of campaign_config that
// matter for contract readiness. Doesn't fetch the existing config — the
// bridge upsert merges only the fields we send, so leaving a field blank
// means "don't touch". On save, the parent should refresh readiness state.
export default function EditCampaignConfigPanel({ campaignId, env, onSaved }: Props) {
  const [open, setOpen] = useState(false);
  const [platforms, setPlatforms] = useState<Record<string, boolean>>({});
  const [count, setCount] = useState<string>('');
  const [audit, setAudit] = useState('');
  const [variantPolicy, setVariantPolicy] = useState('');
  const [paidCeiling, setPaidCeiling] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    setMsg(null);
    const payload: Record<string, unknown> = { env };
    const pickedPlatforms = Object.entries(platforms).filter(([, v]) => v).map(([k]) => k);
    if (pickedPlatforms.length > 0) payload.deliverable_platforms = pickedPlatforms;
    if (count.trim()) {
      const n = Number(count);
      if (!Number.isFinite(n) || n < 1) {
        setErr('deliverable_count_per_platform 必须是 ≥1 的数字');
        setBusy(false);
        return;
      }
      payload.deliverable_count_per_platform = n;
    }
    if (audit.trim()) payload.audit_standards_md = audit.trim();
    if (variantPolicy.trim()) payload.color_variant_policy = variantPolicy.trim();
    if (paidCeiling.trim()) {
      const n = Number(paidCeiling);
      if (!Number.isFinite(n) || n <= 0) {
        setErr('paid_ceiling 必须是 >0 的数字');
        setBusy(false);
        return;
      }
      payload.paid_ceiling = n;
    }
    if (Object.keys(payload).length <= 1) {
      setErr('请至少修改一个字段');
      setBusy(false);
      return;
    }
    try {
      const r = await api.patch<{ patched?: string[] }>(
        `/campaigns/${encodeURIComponent(campaignId)}/config`,
        payload,
      );
      setMsg(`已保存：${(r.patched ?? []).join(', ') || '(no fields)'}`);
      onSaved?.();
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded border border-slate-200">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-2 py-1 text-left text-xs font-medium text-slate-700 hover:bg-slate-50"
      >
        <span>{open ? '▾' : '▸'} 编辑 campaign_config (deliverables / audit / variant policy)</span>
        {!open && <span className="text-[11px] text-slate-400">点击展开</span>}
      </button>
      {open && (
        <form onSubmit={submit} className="space-y-2 border-t border-slate-200 p-2 text-xs">
          <div>
            <div className="text-[11px] text-slate-500">Deliverable platforms (留空 = 不修改)</div>
            <div className="flex flex-wrap gap-2">
              {PLATFORM_CHOICES.map((plat) => (
                <label key={plat} className="inline-flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={!!platforms[plat]}
                    onChange={(e) =>
                      setPlatforms((prev) => ({ ...prev, [plat]: e.target.checked }))
                    }
                  />
                  {plat}
                </label>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
            <label className="flex flex-col">
              <span className="text-slate-500">deliverable_count_per_platform</span>
              <input
                type="number"
                min={1}
                max={20}
                value={count}
                onChange={(e) => setCount(e.target.value)}
                className="rounded border px-2 py-1"
              />
            </label>
            <label className="flex flex-col">
              <span className="text-slate-500">paid_ceiling (USD)</span>
              <input
                type="number"
                min={0}
                step="0.01"
                value={paidCeiling}
                onChange={(e) => setPaidCeiling(e.target.value)}
                className="rounded border px-2 py-1"
              />
            </label>
            <label className="flex flex-col">
              <span className="text-slate-500">color_variant_policy</span>
              <input
                value={variantPolicy}
                onChange={(e) => setVariantPolicy(e.target.value)}
                placeholder="e.g. operator_selected: walnut | oak"
                className="rounded border px-2 py-1"
              />
            </label>
          </div>
          <label className="flex flex-col">
            <span className="text-slate-500">
              audit_standards_md (≥30 字符；留空 = 不修改)
            </span>
            <textarea
              value={audit}
              onChange={(e) => setAudit(e.target.value)}
              rows={3}
              className="rounded border px-2 py-1 font-mono"
            />
          </label>
          <div className="flex items-center justify-between">
            <div>
              {err && <span className="text-rose-700">{err}</span>}
              {msg && <span className="text-emerald-700">{msg}</span>}
            </div>
            <button
              type="submit"
              disabled={busy}
              className="rounded bg-emerald-600 px-3 py-1 font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {busy ? '保存中…' : 'Save patch'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
