# Manual labeling

The labeling UI and workflow scripts are public source code. Training datasets,
human answers, source books and text, audio, and derived corpora stay local and
are excluded from public Git and source releases. This includes the entire
`manual-labeling/`, `training-work/`, `corpora/` and `content/` directories. Do
not add book excerpts, corpus examples or copied data to public documentation,
fixtures or release assets.

The public tooling includes the review UI in `scripts/book1_bucket_review.js`,
the review server in `scripts/serve_label_pilot.py`, label resolution in
`scripts/review_label_resolution.py`, and archive preservation and verification
in `scripts/manual_labeling_archive.py`. These tools do not supply a dataset.

Maintainer workflows that need existing reviews must provision the complete
`manual-labeling/` directory and required working data from a private backup.
Read its local `README.md` before labeling or training. Preserve canonical human
answers, checksums and referenced media; generated proposals must not replace
human choices. Keep private backups of the complete directory, including files
ignored by Git. Removing data from Git tracking must leave these local files
intact.

Git ignore rules prevent new untracked files from being added normally. They do
not untrack files already committed or remove copies from Git history. Review
the tracked file list and source archive contents before publication; any
history cleanup is a separate operation that must also preserve local data.
