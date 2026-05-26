import { create } from 'zustand';

// Imperative confirm() / prompt() built on a zustand-backed queue. Pages
// keep their existing call-site shape (`if (await confirm(...))`)
// instead of having to render a modal component conditionally — and we
// get a proper styled dialog instead of native window.* prompts.

export type DialogVariant = 'info' | 'danger';

export type ConfirmOptions = {
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: DialogVariant;
  // When true, the dialog renders a red LIVE-warning banner. Pages call
  // this with `liveWarning: env === 'LIVE'` for irreversible actions.
  liveWarning?: boolean;
};

export type PromptOptions = ConfirmOptions & {
  placeholder?: string;
  defaultValue?: string;
  multiline?: boolean;
  // When true, an empty submission is rejected by the dialog itself
  // (Submit button stays disabled).
  required?: boolean;
};

type ConfirmRequest = ConfirmOptions & { resolve: (b: boolean) => void };
type PromptRequest = PromptOptions & { resolve: (v: string | null) => void };

type DialogState = {
  confirmReq: ConfirmRequest | null;
  promptReq: PromptRequest | null;
  // Imperative API — pages call these.
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
  prompt: (opts: PromptOptions) => Promise<string | null>;
  // Resolve helpers — DialogHost calls these from button handlers.
  resolveConfirm: (value: boolean) => void;
  resolvePrompt: (value: string | null) => void;
};

export const useDialogStore = create<DialogState>((set, get) => ({
  confirmReq: null,
  promptReq: null,
  confirm: (opts) =>
    new Promise<boolean>((resolve) => {
      set({ confirmReq: { ...opts, resolve } });
    }),
  prompt: (opts) =>
    new Promise<string | null>((resolve) => {
      set({ promptReq: { ...opts, resolve } });
    }),
  resolveConfirm: (value) => {
    const req = get().confirmReq;
    if (!req) return;
    req.resolve(value);
    set({ confirmReq: null });
  },
  resolvePrompt: (value) => {
    const req = get().promptReq;
    if (!req) return;
    req.resolve(value);
    set({ promptReq: null });
  },
}));

// Non-component callers (e.g. inside fetch wrappers) use this handle.
export const dialog = {
  confirm: (opts: ConfirmOptions) => useDialogStore.getState().confirm(opts),
  prompt: (opts: PromptOptions) => useDialogStore.getState().prompt(opts),
};
