export type BookAssetLoader = (id:string) => Promise<Blob>;

export function hydrateBookAssets(root:ParentNode, load:BookAssetLoader, unavailable='Image unavailable'):()=>void {
  let active = true;
  const urls = new Set<string>();
  const pending = new Map<string,Promise<string|null>>();
  const images = [...root.querySelectorAll<Element>('img[data-asset], image[data-asset]')]
    .filter(image => (image.localName === 'img' && image.namespaceURI === 'http://www.w3.org/1999/xhtml')
      || (image.localName === 'image' && image.namespaceURI === 'http://www.w3.org/2000/svg'));
  for (const image of images) {
    image.removeAttribute('src');
    image.removeAttribute('href');
    image.removeAttributeNS('http://www.w3.org/1999/xlink', 'href');
    const id = image.getAttribute('data-asset') || '';
    if (!/^[a-f0-9]{64}\.(?:png|jpg)$/.test(id)) continue;
    if (!pending.has(id)) {
      pending.set(id, Promise.resolve().then(() => active ? load(id) : null).then(blob => {
        if (!active || !blob) return null;
        const url = URL.createObjectURL(blob);
        urls.add(url);
        return url;
      }));
    }
    void pending.get(id)!.then(url => {
      if (active && url) image.setAttribute(image.localName === 'image' ? 'href' : 'src', url);
    }).catch(() => {
      if (!active) return;
      if (image.localName === 'img' && !image.getAttribute('alt')) image.setAttribute('alt', unavailable);
      if (image.localName === 'image') image.setAttribute('aria-label', unavailable);
    });
  }
  return () => {
    active = false;
    for (const image of images) {
      image.removeAttribute('src');
      image.removeAttribute('href');
    }
    urls.forEach(url => URL.revokeObjectURL(url));
    urls.clear();
  };
}
