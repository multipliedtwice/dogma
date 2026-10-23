const FOCUSABLE = 'button:not(:disabled),[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])';

export function removalFocus(opener: HTMLElement | null, row: string, fallbacks: () => Array<HTMLElement | null>): () => HTMLElement | null {
  const item = opener?.closest(row);
  const neighbours = [item?.nextElementSibling, item?.previousElementSibling]
    .map(node => node?.matches(row) ? node.querySelector<HTMLElement>(FOCUSABLE) : null);
  return () => {
    if (!opener || opener.isConnected) return null;
    const target = [...neighbours, ...fallbacks()].find(node => node?.isConnected && !node.matches(':disabled')) ?? null;
    target?.focus({preventScroll: true});
    return target;
  };
}

export function focusIfLost(target: () => HTMLElement | null | undefined): void {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const current = document.activeElement;
    if (current instanceof HTMLElement && current !== document.body && current.isConnected && !current.matches(':disabled')) return;
    target()?.focus({preventScroll: true});
  }));
}
