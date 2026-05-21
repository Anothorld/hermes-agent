import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';

type CampaignBody = {
  campaign_id: string;
  title?: string;
  paid_ceiling?: number;
  commission_band?: { min: number; max: number };
  sku_whitelist?: string[];
  deliverable_count_per_platform?: Record<string, number>;
  contract_required?: boolean;
};

/**
 * Lightweight campaign creation wizard. Operators paste structured JSON
 * (or fill the simple fields) and submit; the bridge upserts the campaign
 * and the user is redirected to the new Kanban view scoped by campaign_id.
 *
 * NL-paste parsing is intentionally kept out of scope — that's the
 * `campaign-intake` skill's job. This wizard is the deterministic CRUD
 * fallback.
 */
export function CampaignWizardPage() {
  const nav = useNavigate();
  const [campaignId, setCampaignId] = useState('');
  const [title, setTitle] = useState('');
  const [paidCeiling, setPaidCeiling] = useState<string>('');
  const [skuWhitelist, setSkuWhitelist] = useState('');
  const [contractRequired, setContractRequired] = useState(true);
  const [extraJson, setExtraJson] = useState('{}');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      let extra: Record<string, unknown> = {};
      try {
        extra = JSON.parse(extraJson || '{}');
      } catch {
        throw new Error('Extra JSON is not valid');
      }
      const body: CampaignBody = {
        campaign_id: campaignId,
        title: title || undefined,
        paid_ceiling: paidCeiling ? Number(paidCeiling) : undefined,
        sku_whitelist: skuWhitelist
          ? skuWhitelist.split(',').map((s) => s.trim()).filter(Boolean)
          : undefined,
        contract_required: contractRequired,
        ...(extra as Partial<CampaignBody>),
      };
      await api.put(`/campaigns/${encodeURIComponent(campaignId)}`, body);
      nav(`/kols?campaign_id=${encodeURIComponent(campaignId)}`);
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold">New Campaign</h1>
      {err && <div className="text-red-600">{err}</div>}
      <div className="grid gap-2 rounded border border-slate-200 bg-white p-3 text-sm">
        <Field label="campaign_id" required>
          <input
            value={campaignId}
            onChange={(e) => setCampaignId(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1"
            placeholder="e.g. ts8319"
          />
        </Field>
        <Field label="title">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1"
          />
        </Field>
        <Field label="paid_ceiling (USD)">
          <input
            type="number"
            value={paidCeiling}
            onChange={(e) => setPaidCeiling(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1"
            placeholder="1500"
          />
        </Field>
        <Field label="sku_whitelist (comma)">
          <input
            value={skuWhitelist}
            onChange={(e) => setSkuWhitelist(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1"
            placeholder="POVI-A1, POVI-B2"
          />
        </Field>
        <Field label="contract_required">
          <input
            type="checkbox"
            checked={contractRequired}
            onChange={(e) => setContractRequired(e.target.checked)}
          />
        </Field>
        <Field label="Extra (JSON)">
          <textarea
            value={extraJson}
            onChange={(e) => setExtraJson(e.target.value)}
            rows={4}
            className="rounded border border-slate-300 px-2 py-1 font-mono text-xs"
            placeholder='{"deliverable_count_per_platform": {"instagram": 3}}'
          />
        </Field>
        <button
          disabled={busy || !campaignId}
          onClick={submit}
          className="self-start rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {busy ? 'Creating…' : 'Create + open Kanban'}
        </button>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
  required,
}: {
  label: string;
  children: React.ReactNode;
  required?: boolean;
}) {
  return (
    <label className="grid grid-cols-[180px_1fr] items-center gap-2">
      <span className="text-xs uppercase tracking-wide text-slate-500">
        {label}
        {required && <span className="ml-0.5 text-red-500">*</span>}
      </span>
      {children}
    </label>
  );
}
