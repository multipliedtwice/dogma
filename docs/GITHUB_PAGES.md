# Public demo website

The site is <https://multipliedtwice.github.io/dogma/>. It is deployed from
`website/` in the `multipliedtwice/dogma` repository through GitHub Actions.

`website/` is the publication boundary. It contains the landing page, player,
existing column mark, a minimal demo catalog, public-domain story captions,
music credits, and deliberately exported MP3s. It contains no application
runtime, model weights, narrator references, training data, labeling records,
private library files or credentials. The deployment artifact contains only
this static directory, rather than the full repository checkout.

New demos use the [one-shot pipeline](PUBLIC_DEMO_ONE_SHOT.md): one generated
take per passage, without automatic recognition, adjudication or retakes.
The English and Russian players expose their matching recordings and original
story text. This is a listening site, not a browser-hosted desktop application.

## Languages, themes and search metadata

English is served at the site root; Russian is served at `ru/`. Both documents
contain translated landing content without JavaScript and header language links.
Each page selects only its matching catalog language: English features
O. Henry's *The Gift of the Magi*; Russian features Chekhov's *Хамелеон*.
An unready catalog entry keeps its player and read-along controls unavailable.
It never falls back to another language's recording or captions. Header and
demo links let listeners choose either language explicitly. Ready recordings
and read-along text remain in their original language, with separate listening
positions for each book. The header theme switch persists the user's
choice; without a saved choice it follows the system light/dark preference.

Each page has its own title, description, canonical URL and sharing PNG, plus
reciprocal `en`, `ru` and `x-default` links. `sitemap.xml` contains only the two
canonical landing URLs. Structured data describes the actual pages/project;
there are no invented prices, ratings or reviews. A project-path `robots.txt`
would not control crawling at the hostname root, so page-level robots metadata
allows indexing instead. Indexing and search previews are controlled by search
engines, not guaranteed by this configuration.

After editing metadata text, run `python scripts/build_website_metadata.py`.
The checked-in 1200×630 PNG cards can be regenerated on the development machine
with `scripts/build_website_social_cards.py` (Pillow and installed fonts). No
font files are distributed. Site checking validates the metadata, alternate
links, sitemap, image dimensions and public-only asset boundary in CI.

## Validate and preview

```sh
python scripts/github_pages.py check --site website
python -m unittest verification.test_github_pages
node --test verification/check_website_languages.mjs
python -m http.server 8080 --bind 127.0.0.1 --directory website
```

Open `http://127.0.0.1:8080/`. Python's basic static server is enough for layout
preview but does not support audio byte ranges; test seeking on GitHub Pages or
a byte-range-capable development server. All site URLs are relative, including
the catalog, MP3s and captions, so the project path `/dogma/` works.

## Preparation and legacy audited export

Use the one-shot entrypoint above for new listening renders. The strict export
command below is retained for explicitly audited historical recordings; it is
not part of one-shot generation and must not certify unchecked audio.

For Russian demo pronunciation, use the development v4 entrypoint after a
source-bound v3 pronunciation checkpoint has prepared the character names and
homograph evidence. It reads that checkpoint and writes a separate generation
workspace before any new synthesis:

```sh
python scripts/prepare_public_demo_pronunciation_v4.py --folder /path/to/source-demo --output /path/to/new-v4-demo --stage prepare
python scripts/prepare_public_demo_pronunciation_v4.py --output /path/to/new-v4-demo --stage freeze --music-path /path/to/verified/music/selected.json
```

This path uses the shared context-aware е/ё layer and requires a context decision
for ambiguous finite-verb readings even when the stress models agree. A plain-е
reading receives an explicit stress mark, such as `узна́ет`; the original book
text and captions stay unchanged. Conflicting stress decisions prevent freezing.
The old v3 entrypoint and existing recordings remain historical evidence. These
commands prepare inputs only; they do not synthesize or retake any audio.

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
private local paths. Footer source links point to
`https://github.com/multipliedtwice/dogma`.

Keep the previous public snapshot available when preparing a new one. The checker
rejects obsolete unreferenced assets; only the selected snapshot belongs in the
publication directory.

## Publish

The GitHub Actions website workflow in `multipliedtwice/dogma` validates the
static site, uploads only `website/` as its Pages artifact, and deploys that
artifact. GitHub Pages uses **GitHub Actions** as its publishing source.
No separate website repository or copy of the application checkout is needed.

Update the selected static assets and generated metadata, run the checks above,
then include the website changes in the repository's normal commit and review
process. The workflow handles publication from its configured branch.

No production backend, model server, external font or analytics service is
required. Model weights, training corpora, protected labels and local library
data remain outside the website publication boundary.

Opening and background music use YuE2-3B, CC BY-NC 4.0. Preserve the visible
attribution and non-commercial-use notice when distributing these recordings.
Story source links and the automated-check/no-human-approval disclosure remain
in the site credits. No new inference runs when the website is built or opened.
