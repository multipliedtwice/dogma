type ClipboardTarget = Pick<Clipboard, 'writeText'> & Partial<Pick<Clipboard, 'write'>>;
type ItemConstructor = new (items: Record<string, Promise<Blob>>) => ClipboardItem;

export function copyWhenReady(text: Promise<string>, clipboard: ClipboardTarget | undefined = globalThis.navigator?.clipboard,
  Item: ItemConstructor | undefined = globalThis.ClipboardItem): Promise<void> {
  if (!clipboard) return text.then(() => { throw new Error('Clipboard is not available.'); });
  const fallback = () => text.then(value => clipboard.writeText(value));
  if (!Item || !clipboard.write) return fallback();
  try {
    return clipboard.write([new Item({'text/plain': text.then(value => new Blob([value], {type: 'text/plain'}))})]).catch(fallback);
  } catch {
    return fallback();
  }
}
