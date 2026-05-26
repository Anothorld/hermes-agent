import { useToastStore } from '../../lib/store';
import { Toast } from './Toast';

// Mount once near App root. Renders the toast queue in a fixed
// bottom-right column; toasts dismiss themselves on timeout (or stay
// sticky for progress kind).

export function ToastHost() {
  const toasts = useToastStore((s) => s.toasts);
  if (toasts.length === 0) return null;
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-40 flex w-full max-w-sm flex-col gap-2">
      {toasts.map((t) => (
        <Toast key={t.id} item={t} />
      ))}
    </div>
  );
}
