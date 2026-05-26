import { useEnvStore } from '../../lib/store';

// Persistent visual cue for the current environment. LIVE renders a
// red strip across the page so operators never forget which side of
// the TEST/LIVE divide they're working on. TEST renders nothing — a
// subtle absence-of-warning is the right signal.

export function EnvBanner() {
  const env = useEnvStore((s) => s.env);
  if (env !== 'LIVE') return null;
  return (
    <div className="sticky top-0 z-30 flex items-center justify-center gap-2 bg-red-600 px-4 py-1 text-xs font-semibold text-white shadow">
      <span aria-hidden>⚠</span>
      <span>LIVE 环境 — 所有写操作会进入正式数据，请谨慎确认</span>
    </div>
  );
}
