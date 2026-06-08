/** Operator-facing labels for Hermes gateway run lifecycle states. */

export const RUN_STATE_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  waiting_for_approval: '等待命令审批',
  stopping: '正在停止',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  evicted: '已结束（网关已清理）',
};

export function runStateLabel(state: string | null | undefined): string {
  if (!state) return '';
  return RUN_STATE_LABEL[state] ?? state;
}

export function isRunStateActive(state: string | null | undefined): boolean {
  if (!state) return false;
  return (
    state === 'queued'
    || state === 'running'
    || state === 'waiting_for_approval'
    || state === 'stopping'
  );
}
