import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { ErrorAlert } from '../components/feedback/ErrorAlert';
import { dialog } from '../components/dialogs/useDialog';
import { useEnvStore, toast } from '../lib/store';
import { errorSummary } from '../lib/errors';

type CampaignBody = {
  campaign_id: string;
  title?: string;
  paid_ceiling?: number;
  commission_band?: { min: number; max: number };
  sku_whitelist?: string[];
  deliverable_platforms?: string[];
  deliverable_count_per_platform?: number;
  contract_required?: boolean;
  test_mode_to?: string;
};

type ParsedDraft = {
  parsed: Partial<CampaignBody>;
  unparsed_lines: string[];
  raw: string;
};

// Campaign creation wizard with two entry modes (NL paste / structured).
// Currently orphaned — entry point in nav redirects to /products which
// runs the per-product creation flow. Kept here for resurrection.
export function CampaignWizardPage() {
  const nav = useNavigate();
  const env = useEnvStore((s) => s.env);
  const [campaignId, setCampaignId] = useState('');
  const [title, setTitle] = useState('');
  const [paidCeiling, setPaidCeiling] = useState<string>('');
  const [skuWhitelist, setSkuWhitelist] = useState('');
  const [contractRequired, setContractRequired] = useState(true);
  const [testModeTo, setTestModeTo] = useState('');
  const [extraJson, setExtraJson] = useState('{}');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const [nlText, setNlText] = useState('');
  const [draft, setDraft] = useState<ParsedDraft | null>(null);
  const [parseBusy, setParseBusy] = useState(false);

  async function parse() {
    setParseBusy(true);
    setErr(null);
    try {
      const r = await api.post<ParsedDraft>('/campaigns/parse', {
        text: nlText,
        env: 'TEST',
      });
      setDraft(r);
    } catch (ex) {
      setErr(ex);
      toast.error('解析失败', errorSummary(ex));
    } finally {
      setParseBusy(false);
    }
  }

  function applyDraft() {
    if (!draft) return;
    const p = draft.parsed;
    if (p.campaign_id) setCampaignId(p.campaign_id);
    if (p.title) setTitle(p.title);
    if (p.paid_ceiling != null) setPaidCeiling(String(p.paid_ceiling));
    if (p.sku_whitelist?.length) setSkuWhitelist(p.sku_whitelist.join(', '));
    if (p.contract_required != null) setContractRequired(p.contract_required);
    if (p.test_mode_to) setTestModeTo(p.test_mode_to);
    const remainder: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(p)) {
      if (
        ['campaign_id', 'title', 'paid_ceiling', 'sku_whitelist',
         'contract_required', 'test_mode_to'].includes(k)
      ) continue;
      remainder[k] = v;
    }
    if (Object.keys(remainder).length > 0) {
      setExtraJson(JSON.stringify(remainder, null, 2));
    }
  }

  async function submit() {
    if (env === 'LIVE') {
      const ok = await dialog.confirm({
        title: '在 LIVE 环境创建 campaign？',
        description: '正式 campaign 将启动 outreach 流程，建议先在 TEST 演练。',
        confirmLabel: '创建',
        cancelLabel: '取消',
        variant: 'danger',
        liveWarning: true,
      });
      if (!ok) return;
    }
    setBusy(true);
    setErr(null);
    try {
      let extra: Record<string, unknown> = {};
      try {
        extra = JSON.parse(extraJson || '{}');
      } catch {
        throw new Error('Extra JSON 不是合法的 JSON');
      }
      const body: CampaignBody = {
        campaign_id: campaignId,
        title: title || undefined,
        paid_ceiling: paidCeiling ? Number(paidCeiling) : undefined,
        sku_whitelist: skuWhitelist
          ? skuWhitelist.split(',').map((s) => s.trim()).filter(Boolean)
          : undefined,
        contract_required: contractRequired,
        test_mode_to: testModeTo || undefined,
        ...(extra as Partial<CampaignBody>),
      };
      await api.put(`/campaigns/${encodeURIComponent(campaignId)}`, body);
      toast.success(`已创建 campaign ${campaignId}`);
      nav(`/campaigns/${encodeURIComponent(campaignId)}/candidates`);
    } catch (ex) {
      setErr(ex);
      toast.error('创建失败', errorSummary(ex));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-editing className="space-y-3">
      <h1 className="text-lg font-semibold">新建 Campaign</h1>
      {!!err && <ErrorAlert error={err} />}

      <div className="rounded border border-slate-200 bg-white p-3 text-sm">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          快速开始：粘贴自然语言简报
        </h2>
        <textarea
          value={nlText}
          onChange={(e) => setNlText(e.target.value)}
          rows={3}
          className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
          placeholder='例：跑 TS8319，预算 1500，IG 5 / TT 5，commission 12%，测试收件 johnny@povison-collab.com'
        />
        <div className="mt-2 flex gap-2">
          <button
            onClick={parse}
            disabled={parseBusy || !nlText.trim()}
            className="rounded border border-sky-600 px-3 py-1 text-sm text-sky-700 hover:bg-sky-50 disabled:opacity-50"
          >
            {parseBusy ? '解析中…' : '解析 → 草稿'}
          </button>
          {draft && (
            <button
              onClick={applyDraft}
              className="rounded bg-sky-600 px-3 py-1 text-sm font-medium text-white hover:bg-sky-700"
            >
              应用到表单 ↓
            </button>
          )}
        </div>
        {draft && (
          <div className="mt-2 space-y-1 rounded bg-slate-50 p-2 text-xs">
            <div className="font-semibold text-slate-700">解析结果：</div>
            <pre className="overflow-x-auto whitespace-pre-wrap text-slate-700">
              {JSON.stringify(draft.parsed, null, 2)}
            </pre>
            {draft.unparsed_lines.length > 0 && (
              <div className="text-amber-700">
                ⚠️ {draft.unparsed_lines.join('; ')}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="grid gap-2 rounded border border-slate-200 bg-white p-3 text-sm">
        <Field label="campaign_id" required>
          <input
            value={campaignId}
            onChange={(e) => setCampaignId(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1"
            placeholder="例：ts8319"
          />
        </Field>
        <Field label="标题">
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
        <Field label="SKU 白名单（逗号分隔）">
          <input
            value={skuWhitelist}
            onChange={(e) => setSkuWhitelist(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1"
            placeholder="POVI-A1, POVI-B2"
          />
        </Field>
        <Field label="测试收件邮箱">
          <input
            value={testModeTo}
            onChange={(e) => setTestModeTo(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1"
            placeholder="johnny@povison-collab.com"
          />
        </Field>
        <Field label="需要合同">
          <input
            type="checkbox"
            checked={contractRequired}
            onChange={(e) => setContractRequired(e.target.checked)}
          />
        </Field>
        <Field label="附加字段 (JSON)">
          <textarea
            value={extraJson}
            onChange={(e) => setExtraJson(e.target.value)}
            rows={4}
            className="rounded border border-slate-300 px-2 py-1 font-mono text-xs"
            placeholder='{"deliverable_platforms": ["instagram"], "deliverable_count_per_platform": 3}'
          />
        </Field>
        <button
          disabled={busy || !campaignId}
          onClick={submit}
          className="self-start rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {busy ? '创建中…' : '创建并打开看板'}
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
