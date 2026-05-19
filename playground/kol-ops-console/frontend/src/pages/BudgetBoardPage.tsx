// Stub page — full implementation deferred. Surfaces budget burn across campaigns
// from CAL negotiation history. Filled in after first LIVE rollout.

export function BudgetBoardPage() {
  return (
    <div className="rounded border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-500">
      <h1 className="mb-2 text-lg font-semibold text-slate-700">Budget board</h1>
      Coming next: per-campaign burndown and floor-violation heatmap, sourced from
      <code className="mx-1 rounded bg-slate-100 px-1">kol_negotiation_history</code>.
    </div>
  );
}
