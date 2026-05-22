import { FormEvent, useMemo, useState } from 'react';
import { api } from '../api';

/**
 * Namespace-aware facts editor (v2.4).
 *
 * Renders 4 tabs (Identity / Offer / Fulfillment / Approval). Each tab
 * shows only fact keys with the matching ``<namespace>.`` prefix. On
 * submit the editor groups entered values by namespace and posts a
 * single ``POST /facts/{identity_id}/multi`` so the bridge can validate
 * + write all namespaces atomically.
 *
 * Identity facts (``identity.*``) are auto-synced by the bridge into the
 * ``kol_identity`` row (see ``cal.write_facts``), so writing e.g.
 * ``identity.primary_email`` here also updates the identity record.
 */

type Namespace = 'identity' | 'offer' | 'fulfillment' | 'approval';

const NS_ORDER: Namespace[] = ['identity', 'offer', 'fulfillment', 'approval'];

const NS_LABEL: Record<Namespace, string> = {
  identity: 'Identity',
  offer: 'Offer',
  fulfillment: 'Fulfillment',
  approval: 'Approval',
};

// Tab badge palette mirrors the rest of the console (Kanban chips,
// ApprovalsPage namespace headers) so the visual contract is uniform.
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
  /** Dotted, namespace-prefixed fact keys to render. Missing-facts list
   *  from goal state is the typical caller. */
  factKeys: string[];
  onSubmitted?: (resp: unknown) => void;
}

export function FactsEditor({ identityId, campaignId, env, factKeys, onSubmitted }: Props) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Group requested keys by namespace once per prop change.
  const groups = useMemo(() => {
    const out: Record<Namespace | 'other', string[]> = {
      identity: [], offer: [], fulfillment: [], approval: [], other: [],
    };
    for (const k of factKeys) out[nsOf(k)].push(k);
    return out;
  }, [factKeys]);

  // Default active tab = first namespace with any keys to collect.
  const [active, setActive] = useState<Namespace>(() => {
    for (const ns of NS_ORDER) if (groups[ns].length > 0) return ns;
    return 'identity';
  });

  if (!factKeys.length) {
    return <div className="text-xs text-slate-500">No facts to collect.</div>;
  }

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const namespaces: Record<string, Record<string, unknown>> = {};
      for (const k of factKeys) {
        const v = values[k];
        if (v === undefined || v === '') continue;
        const ns = nsOf(k);
        // 'other' is a UI bucket only; the bridge rejects unknown namespaces
        // so we surface those to the user instead of silently dropping.
        if (ns === 'other') {
          throw new Error(`fact key '${k}' has no recognised namespace prefix`);
        }
        namespaces[ns] = namespaces[ns] || {};
        namespaces[ns][k] = coerce(v);
      }
      if (Object.keys(namespaces).length === 0) {
        setErr('Fill in at least one value.');
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
    } catch (ex) {
      setErr(String(ex));
    } finally {
      setBusy(false);
    }
  }

  const currentKeys = groups[active] || [];

  return (
    <form onSubmit={submit} className="space-y-2 rounded border border-slate-200 bg-white p-3">
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
              title={`${count} field(s)`}
            >
              {NS_LABEL[ns]}{count > 0 && <span className="ml-1 text-[10px]">({count})</span>}
            </button>
          );
        })}
        {groups.other.length > 0 && (
          <span className="ml-2 text-[10px] text-rose-600" title={groups.other.join(', ')}>
            ⚠ {groups.other.length} unrecognised
          </span>
        )}
      </div>

      {currentKeys.length === 0 ? (
        <div className="px-1 py-2 text-xs text-slate-400">
          No {NS_LABEL[active]} facts requested.
        </div>
      ) : (
        currentKeys.map((k) => (
          <label key={k} className="flex items-center gap-2 text-sm">
            <span className="w-56 shrink-0 font-mono text-xs text-slate-700">{k}</span>
            <input
              value={values[k] ?? ''}
              onChange={(e) => setValues((v) => ({ ...v, [k]: e.target.value }))}
              className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
              placeholder="value (string / number / true|false / [a,b])"
            />
          </label>
        ))
      )}

      {err && <div className="text-xs text-red-600">{err}</div>}
      <button
        type="submit"
        disabled={busy}
        className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
      >
        {busy ? 'Saving…' : 'Save facts'}
      </button>
      <div className="text-[10px] text-slate-400">
        Identity facts are auto-mirrored to <code>kol_identity</code> by the bridge.
      </div>
    </form>
  );
}

/** Coerce string input into JSON-friendly value: bool, int, float, list, str. */
function coerce(s: string): unknown {
  const t = s.trim();
  if (t === 'true') return true;
  if (t === 'false') return false;
  if (/^-?\d+$/.test(t)) return parseInt(t, 10);
  if (/^-?\d+\.\d+$/.test(t)) return parseFloat(t);
  if (t.startsWith('[') && t.endsWith(']')) {
    return t
      .slice(1, -1)
      .split(',')
      .map((x) => x.trim())
      .filter((x) => x.length > 0);
  }
  return s;
}
