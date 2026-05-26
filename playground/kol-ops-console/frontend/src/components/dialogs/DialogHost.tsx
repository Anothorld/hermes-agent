import { ConfirmDialog } from './ConfirmDialog';
import { PromptDialog } from './PromptDialog';
import { useDialogStore } from './useDialog';

// Mount-once host for the imperative dialog API. App root renders this
// next to ToastHost — pages call dialog.confirm() / dialog.prompt() and
// the host renders the active request.

export function DialogHost() {
  const confirmReq = useDialogStore((s) => s.confirmReq);
  const promptReq = useDialogStore((s) => s.promptReq);
  const resolveConfirm = useDialogStore((s) => s.resolveConfirm);
  const resolvePrompt = useDialogStore((s) => s.resolvePrompt);

  return (
    <>
      {confirmReq && (
        <ConfirmDialog
          {...confirmReq}
          onCancel={() => resolveConfirm(false)}
          onConfirm={() => resolveConfirm(true)}
        />
      )}
      {promptReq && (
        <PromptDialog
          {...promptReq}
          onCancel={() => resolvePrompt(null)}
          onSubmit={(v) => resolvePrompt(v)}
        />
      )}
    </>
  );
}
