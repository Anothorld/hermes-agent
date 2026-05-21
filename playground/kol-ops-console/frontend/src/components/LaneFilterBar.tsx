/**
 * Lane filter toolbar used by the Kanban page.
 *
 * Renders chip-style buttons for `all | commerce | fulfillment | publish
 * | meta` plus a "repeat KOL only" toggle. Pure presentational
 * component; parent owns selection state.
 */
import type { Lane } from '../api';

export type LaneFilter = 'all' | Lane;

const LANE_LABEL: Record<LaneFilter, string> = {
  all: 'All',
  commerce: 'Commerce',
  fulfillment: 'Fulfillment',
  publish: 'Publish',
  meta: 'Meta',
};

const LANE_COLOR: Record<LaneFilter, string> = {
  all: 'bg-slate-200 text-slate-700',
  commerce: 'bg-sky-200 text-sky-800',
  fulfillment: 'bg-amber-200 text-amber-800',
  publish: 'bg-emerald-200 text-emerald-800',
  meta: 'bg-slate-300 text-slate-700',
};

interface Props {
  lane: LaneFilter;
  onLaneChange: (lane: LaneFilter) => void;
  repeatOnly: boolean;
  onRepeatOnlyChange: (v: boolean) => void;
}

export function LaneFilterBar({
  lane,
  onLaneChange,
  repeatOnly,
  onRepeatOnlyChange,
}: Props) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <span className="text-slate-500">Lane:</span>
      {(['all', 'commerce', 'fulfillment', 'publish', 'meta'] as LaneFilter[]).map(
        (l) => {
          const active = l === lane;
          const base = active ? LANE_COLOR[l] : 'bg-white text-slate-500 border border-slate-300';
          return (
            <button
              key={l}
              onClick={() => onLaneChange(l)}
              className={`rounded px-2 py-0.5 ${base}`}
            >
              {LANE_LABEL[l]}
            </button>
          );
        },
      )}
      <label className="ml-2 flex items-center gap-1 text-slate-600">
        <input
          type="checkbox"
          checked={repeatOnly}
          onChange={(e) => onRepeatOnlyChange(e.target.checked)}
        />
        仅看老朋友
      </label>
    </div>
  );
}
