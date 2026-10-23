type Focusable = {isConnected: boolean; focus: (options?: FocusOptions) => void; matches: (selector: string) => boolean};

export function createDialogFocus<T extends Focusable>() {
  let opener: T | null = null;
  let open = false;
  return {
    change(next: boolean, candidate: T | null) {
      if (next && !open) opener = candidate;
      open = next;
    },
    restore(fallback: T | null, focused: () => unknown, preferred: T | null = null): boolean {
      if (open) return false;
      const target = opener;
      opener = null;
      for (const element of [preferred, target, fallback]) {
        if (!element?.isConnected || element.matches(':disabled')) continue;
        element.focus({preventScroll: true});
        if (focused() === element) return true;
      }
      return false;
    },
  };
}
