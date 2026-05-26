import { useCallback } from 'react';
import { dialog } from '../components/dialogs/useDialog';
import type { ConfirmOptions, PromptOptions } from '../components/dialogs/useDialog';

// Thin hooks for components that prefer hook-style call sites over the
// imperative `dialog.*` handle. They return memoised wrappers.

export function useConfirm() {
  return useCallback((opts: ConfirmOptions) => dialog.confirm(opts), []);
}

export function usePrompt() {
  return useCallback((opts: PromptOptions) => dialog.prompt(opts), []);
}
