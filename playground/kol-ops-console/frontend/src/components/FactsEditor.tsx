import { FormEvent, useMemo, useState } from 'react';
import { api } from '../api';
import { FactInput } from './inputs/FactInput';
import { FactKeyChip } from './inputs/FactKeyChip';
import { ErrorAlert } from './feedback/ErrorAlert';
import { toast } from '../lib/store';
import { errorSummary } from '../lib/errors';

// Namespace-aware facts editor. Renders 4 tabs (Identity / Offer /
// Fulfillment / Approval); each tab shows only fact keys with the
// matching ``<namespace>.`` prefix and uses FactInput for the
// type-aware input control (toggle / select / number / etc). Submits
// via the multi-namespace endpoint atomically.

type Namespace = 'identity' | 'offer' | 'fulfillment' | 'approval';

const NS_ORDER: Namespace[] = ['identity', 'offer', 'fulfillment', 'approval'];

const NS_LABEL: Record<Namespace, string> = {
  identity: '身份',
  offer: '合作',
  fulfillment: '物流',
  approval: '审批',
};

const NS_TAB_COLOR: Record<Namespace, string> = {
  identity: 'border-sky-400 text-sky-800 bg-sky-50',
  offer: 'border-emerald-400 text-emerald-800 bg-emerald-50',
  fulfillment: 'border-amber-400 text-amber-800 bg-amber-50',
  approval: 'border-rose-400 text-rose-800 bg-rose-50',
};

function nsOf(key: string): Namespace | 'other' {
  const head = key.split('.', 1)[0];
  return (NS_ORDER as string[]).includes(head) ? (head as Namespace) : 'other';
}

interface Props {
  identityId: number;
  campaignId?: string;
  env?: 'TEST' | 'LIVE';
  /** Dotted, namespace-prefixed fact keys to render. */
  factKeys: string[];
  onSubmitted?: (resp: unknown) => void;
}

export function FactsEditor({ identityId, campaignId, env, factKeys, onSubmitted }: Props) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const groups = useMemo(() => {
    const out: Record<Namespace | 'other', string[]> = {
      identity: [], offer: [], fulfillment: [], approval: [], other: [],
    };
    for (const k of factKeys) out[nsOf(k)].push(k);
    return out;
  }, [factKeys]);

  const [active, setActive] = useState<Namespace>(() => {
    for (const ns of NS_ORDER) if (groups[ns].length > 0) return ns;
    return 'identity';
  });

  if (!factKeys.length) {
    return <div className="text-xs text-slate-500">没有待补全的字段。</div>;
  }

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const namespaces: Record<string, Record<string, unknown>> = {};
      for (const k of factKeys) {
        const v = values[k];
        if (v === undefined || v === '' || v === null) continue;
        const ns = nsOf(k);
        if (ns === 'other') {
          throw new Error(`字段 '${k}' 没有合法的命名空间前缀`);
        }
        namespaces[ns] = namespaces[ns] || {};
        namespaces[ns][k] = v;
      }
      if (Object.keys(namespaces).length === 0) {
        setErr(new Error('请至少填一项'));
        setBusy(false);
        return;
      }
      const body = {
        campaign_id: campaignId,
        env,
        source: 'console',
        namespaces,
      };
      const out = await api.post<unknown>(`/facts/${identityId}/multi`, body);
      onSubmitted?.(out);
      setValues({});
      toast.success('字段已保存', `${Object.keys(namespaces).length} 个 namespace 更新`);
    } catch (ex) {
      setErr(ex);
      toast.error('保存失败', errorSummary(ex));
    } finally {
      setBusy(false);
    }
  }

  const currentKeys = groups[active] || [];

  return (
    <form
      onSubmit={submit}
      data-editing
      className="space-y-2 rounded border border-slate-200 bg-white p-3"
    >
      <div className="flex items-center gap-1">
        {NS_ORDER.map((ns) => {
          const count = groups[ns].length;
          const isActive = ns === active;
          const base = isActive
            ? `border-b-2 ${NS_TAB_COLOR[ns]}`
            : 'border-b-2 border-transparent text-slate-500 hover:text-slate-700';
          return (
            <button
              key={ns}
              type="button"
              onClick={() => setActive(ns)}
              className={`px-2 py-1 text-xs font-medium ${base}`}
              title={`${count} 个字段`}
            >
              {NS_LABEL[ns]}
              {count > 0 && <span className="ml-1 text-[10px]">({count})</span>}
            </button>
          );
        })}
        {groups.other.length > 0 && (
          <span
            className="ml-2 text-[10px] text-rose-600"
            title={groups.other.join(', ')}
          >
            ⚠ {groups.other.length} 个字段名不识别
          </span>
        )}
      </div>

      {currentKeys.length === 0 ? (
        <div className="px-1 py-2 text-xs text-slate-400">
          {NS_LABEL[active]}下没有待补全字段。
        </div>
      ) : (
        currentKeys.map((k) => (
          <div key={k} className="flex items-start gap-2">
            <FactKeyChip factKey={k} variant="filled" className="mt-1 w-40 shrink-0 truncate" />
            <div className="flex-1">
              <FactInput
                factKey={k}
                value={values[k] ?? ''}
                onChange={(v) => setValues((m) => ({ ...m, [k]: v }))}
                bare
              />
            </div>
          </div>
        ))
      )}

      {!!err && <ErrorAlert error={err} compact />}
      <button
        type="submit"
        disabled={busy}
        className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
      >
        {busy ? '保存中…' : '保存字段'}
      </button>
      <div className="text-[10px] text-slate-400">
        身份类字段会自动同步到 KOL 档案。
      </div>
    </form>
  );
}
