# Public demo website

The Dogma product repository stays private. The public site is
<https://multipliedtwice.github.io/dogma-demo/>; its separate repository,
`multipliedtwice/dogma-demo`, contains only static website assets.
The current GitHub plan does not support Pages from the private product repo.

`website/` is the publication boundary. It contains the landing page, player,
existing column mark, a minimal demo catalog, public-domain story captions,
music credits, and deliberately exported MP3s. It contains no application
runtime, model weights, narrator references, training data, labeling records,
private library files or credentials. There is no automatic sync of the product
repository into the public repository.

The English demo is available. The Russian entry remains in preparation and
exposes no recording or captions until its completed export is ready. This is
a listening site, not a browser-hosted copy of the desktop application.

## Validate and preview

```sh
python scripts/github_pages.py check --site website
python -m unittest verification.test_github_pages
python -m http.server 8080 --bind 127.0.0.1 --directory website
```

Open `http://127.0.0.1:8080/`. Python's basic static server is enough for layout
preview but does not support audio byte ranges; test seeking on GitHub Pages or
a byte-range-capable development server. All site URLs are relative, including
the catalog, MP3s and captions, so the project path `/dogma-demo/` works.

## Export a completed local recording

Run in the existing Python environment against a completed local demo workspace:

```sh
python scripts/github_pages.py export --book /path/to/completed-demo --language en --duration 723.296042
python scripts/github_pages.py check --site website
```

Export validates the completed publication through
`scripts/package_public_audiobook_demos.py` before copying a single selected MP3
and projecting caption fields. It never copies the workspace or its public
directory wholesale. Duration is measured from the actual MP3. Audio and caption
filenames include their byte hashes. Unready catalog entries cannot reference
audio. The checker rejects unselected files, altered asset hashes, linked paths,
private local paths and links to the private product repository.

Keep the previous public snapshot available when preparing a new one. The checker
rejects obsolete unreferenced assets; only the selected snapshot belongs in the
publication directory.

## Publish

The private repository's `Public website checks` workflow validates the static
site; it does not publish the private repository or store deployment credentials.
After validation, copy only `website/` contents into a clean checkout of the
public `dogma-demo` repository, inspect the complete file list, and commit/push
that static snapshot to its `main` branch. Never copy the product checkout.

The public repository uses GitHub Pages **Deploy from a branch**, `main`, `/`
(root), with `.nojekyll`. No production backend, model server, external font,
analytics service or secret is required. Changes to the product source remain
private unless a maintainer explicitly makes a separate publication decision.

Opening and background music use YuE2-3B, CC BY-NC 4.0. Preserve the visible
attribution and non-commercial-use notice when distributing these recordings.
Story source links and the automated-check/no-human-approval disclosure remain
in the site credits. No new inference runs when the website is built or opened.
