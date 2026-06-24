import {
  QUEUE_SORT_LABEL,
  QUEUE_SORT_MODES,
  type QueueSortMode,
} from '../../lib/queueSort';

type Props = {
  value: QueueSortMode;
  onChange: (mode: QueueSortMode) => void;
  className?: string;
};

export function QueueSortSelect({ value, onChange, className }: Props) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as QueueSortMode)}
      className={className ?? 'rounded border border-slate-300 px-2 py-1 text-sm'}
      title="列表排序方式"
    >
      {QUEUE_SORT_MODES.map((mode) => (
        <option key={mode} value={mode}>
          {QUEUE_SORT_LABEL[mode]}
        </option>
      ))}
    </select>
  );
}
