import { useEffect, useState } from 'react';
import { api, CampaignListItem } from '../../api';
import { useCampaignStore, useEnvStore } from '../../lib/store';

// Global campaign picker bound to useCampaignStore. Mounted in
// GlobalNav so every campaign-scoped page reads from the same source.
// URL ?campaign_id= still wins on first load (deep-link friendly) —
// pages should sync the URL value into the store on mount.

export function CampaignPicker() {
  const env = useEnvStore((s) => s.env);
  const campaignId = useCampaignStore((s) => s.currentCampaignId);
  const setCampaignId = useCampaignStore((s) => s.setCampaignId);
  const [campaigns, setCampaigns] = useState<CampaignListItem[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoaded(false);
    api
      .get<{ items: CampaignListItem[] }>('/campaigns')
      .then((r) => {
        if (!cancelled) setCampaigns(r.items || []);
      })
      .catch(() => {
        if (!cancelled) setCampaigns([]);
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const picker = campaigns.filter((c) => c.env === env);
  const known = picker.some((c) => c.campaign_id === campaignId);

  return (
    <label className="flex items-center gap-1 text-xs" title="当前 campaign">
      <span className="text-slate-500">campaign</span>
      <select
        value={known ? campaignId : ''}
        onChange={(e) => setCampaignId(e.target.value)}
        className="max-w-[14rem] rounded border border-slate-300 bg-white px-2 py-0.5"
      >
        <option value="">
          {loaded
            ? picker.length
              ? '— 选择 campaign —'
              : `(${env} 暂无 campaign)`
            : '加载中…'}
        </option>
        {picker.map((c) => (
          <option key={`${c.campaign_id}|${c.env}`} value={c.campaign_id}>
            {c.campaign_id} · {c.candidate_count} kol
            {c.status && c.status !== 'draft' ? ` · ${c.status}` : ''}
          </option>
        ))}
      </select>
    </label>
  );
}
