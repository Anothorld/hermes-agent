// Tiny unread red-dot. No number, no label — count chips elsewhere
// already carry that info; this dot only signals "there's something
// new since you last looked." Defaults to inline (sits next to text);
// pass mode="corner" to absolutely position at the top-right of a
// relatively-positioned parent.

type Props = {
  show: boolean;
  mode?: 'inline' | 'corner';
  title?: string;
};

export function UnreadDot({ show, mode = 'inline', title }: Props) {
  if (!show) return null;
  if (mode === 'corner') {
    return (
      <span
        aria-label={title || '有未读更新'}
        title={title || '有未读更新'}
        className="pointer-events-none absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-rose-500 ring-2 ring-white"
      />
    );
  }
  return (
    <span
      aria-label={title || '有未读更新'}
      title={title || '有未读更新'}
      className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-rose-500 align-middle"
    />
  );
}
