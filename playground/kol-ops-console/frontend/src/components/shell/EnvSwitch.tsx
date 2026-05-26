import { dialog } from '../dialogs/useDialog';
import { useEnvStore } from '../../lib/store';

// Single source of truth for TEST/LIVE in the nav. Switching INTO LIVE
// pops a confirm; switching INTO TEST is silent (lower-stakes
// direction).

export function EnvSwitch() {
  const env = useEnvStore((s) => s.env);
  const setEnv = useEnvStore((s) => s.setEnv);

  async function change(next: 'TEST' | 'LIVE') {
    if (next === env) return;
    if (next === 'LIVE') {
      const ok = await dialog.confirm({
        title: '切换到 LIVE 环境？',
        description:
          '切换后，本会话所有页面（看板 / 审批 / 升级 / 等等）都会指向正式数据。误操作不可撤销，请确认。',
        confirmLabel: '切换到 LIVE',
        cancelLabel: '保持 TEST',
        variant: 'danger',
        liveWarning: true,
      });
      if (!ok) return;
    }
    setEnv(next);
  }

  const cls =
    env === 'LIVE'
      ? 'border-red-300 bg-red-50 text-red-700'
      : 'border-slate-300 bg-white text-slate-700';

  return (
    <label className="flex items-center gap-1 text-xs">
      <span className="text-slate-500">env</span>
      <select
        value={env}
        onChange={(e) => change(e.target.value as 'TEST' | 'LIVE')}
        className={`rounded border px-2 py-0.5 font-medium ${cls}`}
      >
        <option value="TEST">TEST</option>
        <option value="LIVE">LIVE</option>
      </select>
    </label>
  );
}
