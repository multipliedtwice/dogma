// Book navigation shares the current origin, including the remote labeling URL.
function initialiseBookNavigation(reviewStore, pausePlayback) {
  const selector = document.querySelector('#book');
  if (!selector) return;
  const books = [
    {value:'book1', label:'Book 1', route:'/character-rb1/fine-tune-all/buckets.html', prefix:'/character-rb1/', localPort:'1437'},
    {value:'book2', label:'Book 2', route:'/character-rb2-full/fine-tune-all/', prefix:'/character-rb2-full/', localPort:'1439'},
    {value:'book3', label:'Book 3', route:'/character-rb3-full/fine-tune-all/', prefix:'/character-rb3-full/', localPort:'1440'}
  ];
  const pilot = {value:'pilot', label:'Book 2 — original pilot', route:'/character-rb2/fine-tune-all/', prefix:'/character-rb2/', localPort:'1438'};
  const currentBook = books.find(book => location.pathname.startsWith(book.prefix))
    || (location.pathname.startsWith(pilot.prefix) ? pilot : null);
  if (!currentBook) return;
  const availableBooks = currentBook.value === 'pilot' ? [...books, pilot] : books;
  selector.replaceChildren(...availableBooks.map(book => new Option(book.label, book.value)));
  const routes = Object.fromEntries(availableBooks.map(book => [book.value, book]));
  const current = currentBook.value;
  selector.value = current;
  selector.disabled = false;
  selector.onchange = async () => {
    const next = selector.value;
    const nextBook = routes[next];
    if (next === current || !nextBook) return;
    selector.disabled = true;
    const surfaces = [...document.querySelectorAll('main, .player-bar')];
    const previousInert = surfaces.map(element => element.inert);
    surfaces.forEach(element => { element.inert = true; });
    const status = document.querySelector('#save-status');
    try {
      pausePlayback();
      if (status) status.textContent = 'Saving before switching books…';
      // save() serializes with a pending autosave. Flush any edits that were
      // made while that earlier request was in flight before leaving the page.
      do { await reviewStore.save(); }
      while (Object.keys(reviewStore.changes()).length || reviewStore.reviewer !== reviewStore.baseReviewer);
      let target = nextBook.route;
      try {
        sessionStorage.setItem('rimsky-book-location-' + current, location.pathname + location.search);
        const saved = sessionStorage.getItem('rimsky-book-location-' + next);
        if (saved && new URL(saved, location.origin).pathname.startsWith(nextBook.prefix))
          target = saved;
      } catch { /* Navigation still works when browser storage is unavailable. */ }
      // Older running servers serve one book. Local navigation can use its
      // existing server until the shared routes are loaded at a normal restart.
      const available = await fetch(target, {method:'HEAD', cache:'no-store'});
      if (!available.ok) {
        if (!['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname))
          throw Error('Switching books through the remote link needs the labeling server restarted. Your edits are saved.');
        const local = new URL(target, location.origin);
        local.port = nextBook.localPort;
        const response = await fetch(local, {method:'HEAD', mode:'no-cors', cache:'no-store'});
        // A different localhost port is opaque; navigation loads the real page.
        if (response.type !== 'opaque' && !response.ok) throw Error('The selected book is unavailable. Your edits are saved.');
        target = local.href;
      }
      location.assign(target);
    } catch (error) {
      selector.value = current;
      selector.disabled = false;
      surfaces.forEach((element, index) => { element.inert = previousInert[index]; });
      if (status) {
        status.textContent = 'Could not switch books. ' + error.message;
        reviewConflictControls(reviewStore, status, () => reviewStore.save());
      }
    }
  };
}
