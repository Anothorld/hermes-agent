import { FormEvent, useEffect, useState } from 'react';
import { api } from '../api';

type Check = {
  ok: boolean;
  value: unknown;
  label: string;
  why?: string | null;
};

type Readiness = {
  campaign_id: string;
  identity_id: number;
  env: string;
  ready: boolean;
  blockers: string[];
  skipped_reason?: string;
  sections: {
    identity: Record<string, Check>;
    shipping_address: Record<string, Check>;
    product: Record<string, Check>;
    campaign: Record<string, Check>;
    offer: Record<string, Check>;
  };
};

const SECTION_LABEL: Record<keyof Readiness['sections'], string> = {
  identity: 'KOL identity',
  shipping_address: '收货信息',
  product: 'Product (SKU / variant / link)',
  campaign: 'Campaign config',
  offer: 'Offer (compensation)',
};

function valueOf(c: Check | undefined): string {
  if (!c) return '';
  const v = c.value;
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'number') return String(v);
  return '';
}

function CheckRow({ k, v }: { k: string; v: Check }) {
  const display = v.value === null || v.value === undefined
    ? '—'
    : typeof v.value === 'object'
      ? JSON.stringify(v.value)
      : String(v.value);
  return (
    <li className="flex items-start gap-2 text-xs">
      <span className={v.ok ? 'text-emerald-600' : 'text-rose-600'}>
        {v.ok ? '✓' : '✗'}
      </span>
      <div className="min-w-0 flex-1">
        <div>
          <span className="font-medium text-slate-700">{v.label}</span>
          <span className="ml-2 font-mono text-[10px] text-slate-400">{k}</span>
        </div>
        <div className={v.ok ? 'text-slate-600' : 'text-rose-700'}>
          {display}
        </div>
        {!v.ok && v.why && (
          <div className="mt-0.5 text-[11px] italic text-rose-600">{v.why}</div>
        )}
      </div>
    </li>
  );
}

type FillForm = {
  full_name: string;
  primary_email: string;
  phone: string;
  display_name: string;
  ship_full_name: string;
  ship_street: string;
  ship_city: string;
  ship_state: string;
  ship_zip: string;
  ship_email: string;
  ship_phone: string;
};

// Prefill what we already know — never blank an existing value.
function seedFillForm(data: Readiness): FillForm {
  const i = data.sections.identity;
  const s = data.sections.shipping_address;
  return {
    full_name: valueOf(i.full_name),
    primary_email: valueOf(i.primary_email),
    phone: valueOf(i.phone),
    display_name: '',
    ship_full_name: valueOf(s.full_name),
    ship_street: valueOf(s.street),
    ship_city: valueOf(s.city),
    ship_state: valueOf(s.state),
    ship_zip: valueOf(s.zip),
    ship_email: valueOf(s.email),
    ship_phone: valueOf(s.phone),
  };
}

function FillBlockersForm({
  data,
  onSaved,
}: {
  data: Readiness;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<FillForm>(() => seedFillForm(data));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  // Re-seed when the underlying readiness changes (e.g. after Recheck).
  useEffect(() => {
    setForm(seedFillForm(data));
  }, [data]);

  const set = (k: keyof FillForm) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((prev) => ({ ...prev, [k]: e.target.value }));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    setMsg(null);
    const trim = (s: string) => s.trim();
    const payload: Record<string, unknown> = {
      identity_id: data.identity_id,
      env: data.env,
      campaign_id: data.campaign_id,
    };
    if (trim(form.full_name)) payload.full_name = trim(form.full_name);
    if (trim(form.primary_email)) payload.primary_email = trim(form.primary_email);
    if (trim(form.phone)) payload.phone = trim(form.phone);
    if (trim(form.display_name)) payload.display_name = trim(form.display_name);
    const shipping: Record<string, string> = {};
    if (trim(form.ship_full_name)) shipping.full_name = trim(form.ship_full_name);
    if (trim(form.ship_street)) shipping.street = trim(form.ship_street);
    if (trim(form.ship_city)) shipping.city = trim(form.ship_city);
    if (trim(form.ship_state)) shipping.state = trim(form.ship_state);
    if (trim(form.ship_zip)) shipping.zip = trim(form.ship_zip);
    if (trim(form.ship_email)) shipping.email = trim(form.ship_email);
    if (trim(form.ship_phone)) shipping.phone = trim(form.ship_phone);
    if (Object.keys(shipping).length > 0) {
      payload.shipping_address = shipping;
    }
    const fieldKeys = Object.keys(payload).filter(
      (k) => !['identity_id', 'env', 'campaign_id'].includes(k),
    );
    if (fieldKeys.length === 0) {
      setErr('请至少填一个字段');
      setBusy(false);
      return;
    }
    try {
      const r = await api.post<{ touched?: string[] }>(
        `/campaigns/${encodeURIComponent(data.campaign_id)}/contract-readiness/fill-blockers`,
        payload,
      );
      setMsg(`已写入：${(r.touched ?? []).join(', ') || '(none)'}`);
      onSaved();
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-2 rounded border border-slate-200 bg-white p-2 text-xs">
      <div className="font-medium text-slate-700">Fix blockers · 直接录入缺失信息</div>
      <fieldset className="space-y-1">
        <legend className="text-[11px] text-slate-500">KOL identity</legend>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <label className="flex flex-col">
            <span className="text-slate-500">Full Name</span>
            <input
              value={form.full_name}
              onChange={set('full_name')}
              className="rounded border px-2 py-1"
            />
          </label>
          <label className="flex flex-col">
            <span className="text-slate-500">Email (kol_identity.primary_email)</span>
            <input
              type="email"
              value={form.primary_email}
              onChange={set('primary_email')}
              className="rounded border px-2 py-1"
            />
          </label>
          <label className="flex flex-col">
            <span className="text-slate-500">Phone</span>
            <input
              value={form.phone}
              onChange={set('phone')}
              className="rounded border px-2 py-1"
            />
          </label>
          <label className="flex flex-col">
            <span className="text-slate-500">Display name (可选)</span>
            <input
              value={form.display_name}
              onChange={set('display_name')}
              className="rounded border px-2 py-1"
            />
          </label>
        </div>
      </fieldset>
      <fieldset className="space-y-1">
        <legend className="text-[11px] text-slate-500">
          收货地址 (Full Name / Street / City / States / Zip Code / Email / Phone Number)
        </legend>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <label className="flex flex-col">
            <span className="text-slate-500">Full Name</span>
            <input
              value={form.ship_full_name}
              onChange={set('ship_full_name')}
              className="rounded border px-2 py-1"
            />
          </label>
          <label className="flex flex-col">
            <span className="text-slate-500">Street</span>
            <input
              value={form.ship_street}
              onChange={set('ship_street')}
              className="rounded border px-2 py-1"
            />
          </label>
          <label className="flex flex-col">
            <span className="text-slate-500">City</span>
            <input
              value={form.ship_city}
              onChange={set('ship_city')}
              className="rounded border px-2 py-1"
            />
          </label>
          <label className="flex flex-col">
            <span className="text-slate-500">State</span>
            <input
              value={form.ship_state}
              onChange={set('ship_state')}
              className="rounded border px-2 py-1"
            />
          </label>
          <label className="flex flex-col">
            <span className="text-slate-500">Zip Code</span>
            <input
              value={form.ship_zip}
              onChange={set('ship_zip')}
              className="rounded border px-2 py-1"
            />
          </label>
          <label className="flex flex-col">
            <span className="text-slate-500">Email</span>
            <input
              type="email"
              value={form.ship_email}
              onChange={set('ship_email')}
              className="rounded border px-2 py-1"
            />
          </label>
          <label className="flex flex-col">
            <span className="text-slate-500">Phone Number</span>
            <input
              value={form.ship_phone}
              onChange={set('ship_phone')}
              className="rounded border px-2 py-1"
            />
          </label>
        </div>
      </fieldset>
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
          {busy ? '保存中…' : 'Save & recheck'}
        </button>
      </div>
    </form>
  );
}

export default function ContractReadinessPanel({
  campaignId,
  identityId,
  env,
}: {
  campaignId: string;
  identityId: number;
  env: string;
}) {
  const [data, setData] = useState<Readiness | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [showFill, setShowFill] = useState(false);

  const refresh = () => {
    setBusy(true);
    setErr(null);
    api
      .get<Readiness>(
        `/campaigns/${encodeURIComponent(campaignId)}/contract-readiness?identity_id=${identityId}&env=${env}`,
      )
      .then(setData)
      .catch((e) => setErr(String(e)))
      .finally(() => setBusy(false));
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaignId, identityId, env]);

  if (err) {
    return (
      <div className="rounded border border-rose-300 bg-rose-50 px-3 py-1 text-xs text-rose-800">
        Contract readiness check failed: {err}
      </div>
    );
  }
  if (!data) {
    return <div className="text-xs text-slate-400">Checking contract readiness…</div>;
  }

  const pillCls = data.ready
    ? 'bg-emerald-100 text-emerald-800'
    : 'bg-rose-100 text-rose-800';
  const blockerCount = data.blockers.length;
  return (
    <div className={`rounded border p-2 ${data.ready ? 'border-emerald-200 bg-emerald-50/40' : 'border-rose-200 bg-rose-50/40'}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="font-medium text-slate-700 hover:text-slate-900"
        >
          {open ? '▾' : '▸'} Contract readiness
        </button>
        <span className={`rounded px-2 py-0.5 ${pillCls}`}>
          {data.ready ? 'READY' : `${blockerCount} blocker${blockerCount === 1 ? '' : 's'}`}
        </span>
        {data.skipped_reason && (
          <span className="rounded bg-slate-200 px-2 py-0.5 text-slate-700">
            {data.skipped_reason}
          </span>
        )}
        {!data.ready && (
          <button
            type="button"
            onClick={() => setShowFill((v) => !v)}
            className="rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-emerald-800 hover:bg-emerald-100"
          >
            {showFill ? '× Cancel fill' : '✎ Fix blockers'}
          </button>
        )}
        <button
          type="button"
          onClick={refresh}
          disabled={busy}
          className="ml-auto rounded border border-slate-300 px-2 py-0.5 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
        >
          {busy ? '…' : 'Recheck'}
        </button>
      </div>
      {!data.ready && !open && (
        <div className="mt-1 text-[11px] text-rose-700">
          Missing: {data.blockers.slice(0, 5).join(', ')}
          {data.blockers.length > 5 && ` +${data.blockers.length - 5} more`}
        </div>
      )}
      {showFill && (
        <div className="mt-2">
          <FillBlockersForm
            data={data}
            onSaved={() => {
              refresh();
              setShowFill(false);
            }}
          />
        </div>
      )}
      {open && (
        <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-2">
          {(Object.entries(data.sections) as [keyof Readiness['sections'], Record<string, Check>][]).map(
            ([sec, checks]) => (
              <div key={sec} className="rounded border border-slate-200 bg-white p-2">
                <div className="mb-1 text-xs font-medium text-slate-500">
                  {SECTION_LABEL[sec]}
                </div>
                <ul className="space-y-1">
                  {Object.entries(checks).map(([k, v]) => (
                    <CheckRow key={k} k={`${sec}.${k}`} v={v} />
                  ))}
                </ul>
              </div>
            ),
          )}
        </div>
      )}
    </div>
  );
}
