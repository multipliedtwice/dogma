# Book 1 bucket labeling

Open <http://127.0.0.1:1437/character-rb1/fine-tune-all/>.
This is the maintained Book 1 labeling UI. **Refresh saved labels** loads changes
made by another tab or device while this page is open.

Each bucket contains one reusable voice family and one mood. Only explicitly reviewed,
valid labels are used. Mixed voices, boundary reports, rejected clips, precision failures,
and basic audio failures remain held. The player uses the precision sentence WAVs.
Character and audio channel remain separate metadata.

## Listening and corrections

- **Play all** plays the current bucket view in order, with a 450 ms gap. **Pause all**
  pauses playback and remembers the exact clip and timestamp. **Resume all** continues
  there; if that clip has moved to another bucket or no longer fits the filter, playback
  skips forward to the next eligible clip rather than starting over. Pausing during the
  gap after a completed clip also resumes at the next clip. Each bucket/filter has its
  own local browser bookmark, preserved across refresh without automatic playback.
  At the end, **Play all again** starts a fresh pass. The narrow player bar stays fixed
  at the top, with page spacing adjusted to its height so content remains accessible.
  Individually played clips can also become the resume point for Play all.
  Follow clip
  changes pages and scrolls to the active clip. It preserves the selected filter and
  dropdown choices on the current page. The native audio timeline supports seeking.
  The local server supports byte-range requests for WAVs as well as source MP3s.
- **Fits this bucket** records the user's consistency check. Playback alone does not approve anything.
- **Confirm listened clips fit** approves clips in the selected bucket that were actually
  played for at least 75% of their duration. Seeking skips do not count as listening.
  Replaying the same section does not count it twice.
- Select another voice or mood and press **Move clip** to correct a misplaced clip.
  This edits that sentence's canonical labels, preserving other labels, notes, and boundary reports.
  The moved card remains on screen until navigation, so it does not disappear while listening.
- **Undo last move** works after refresh in the same tab. It restores only label fields
  and refuses to erase subsequent label or verification changes.
- Comments save separately from the original passage notes. Changes save automatically;
  check for **Saved locally**. Export review JSON makes an additional backup.

Bucket, filter, and page are stored in the URL. Browser Back and Forward restore the view.
The review server applies atomic changes per field instead of replacing the complete draft.
Independent edits from other tabs merge; conflicting edits to the same field return a visible
conflict rather than overwrite answers. Unsaved edits remain in that tab's local storage.
Older pages that still send full snapshots cannot overwrite a newer clip revision.
If an older snapshot contains conflicting edits, saving fails visibly instead of silently
skipping those edits. The conflict controls let you keep your changes or use the latest saved labels.

## Audio comparison warnings

Every measured clip shows **Pace estimate** and **Average volume**, even without a
warning. Both are compared with the mean of measured clips from other paragraphs
in the current voice + mood bucket. These visible averages include short clips;
the statistical warning screen below still requires clips of at least three seconds.
Moving a clip recalculates its comparisons using its new bucket.
Pace counts sound envelope peaks per second, not words or exact syllables. Average
volume is whole-clip RMS in dBFS, including pauses and edge padding, not LUFS or a
perceptual loudness score. Values closer to zero mean louder recordings. Warning
details use the existing median active-frame speech volume, labeled **Typical
speech volume**, which differs from the whole-clip average.

Clip headers show **Unusual for this bucket** when waveform measurements differ
strongly from the current voice family + mood group. Open **Why this clip is unusual**
to see the measured pitch, pitch range, beginning pitch, ending pitch, final rise/fall,
volume, volume changes, pauses, or pace
estimate compared with the average of clips from other paragraphs in that bucket.
Text meaning is not used to decide these warnings. Label the sound Vlad actually makes,
even when the book suggests a different emotion.

Warnings recalculate after moves, undo, saves, and refresh. They never change labels,
approve clips, or hold them out of training. A warning can be caused by performance
variation or a measurement error; absence of a warning does not validate a label.
Clips with warnings appear first in the filtered bucket, before pagination. Both
the warning group and the remaining group keep source order. New Play all queues
follow this display order; existing playback queues retain their saved position.
Moving a clip keeps its current card visible until page navigation as before.
The comparison needs at least eight measured clips, seven source paragraphs, and
measurements for half the bucket. Clips shorter than three seconds are omitted.
For each comparison, there must be seven other clips from six other paragraphs.
The warning needs two measures above 2.5 standard deviations or one above three,
using minimum variation floors to avoid treating tiny differences as errors.
The related beginning, ending and shape measurements count as one contour cue;
they cannot trigger a joint warning just by counting the same effect twice.
Pitch is omitted when the waveform has insufficient periodic evidence. The pace
measure counts sound envelope peaks; it is not a precise syllable or word rate.
Features are checked against the exact WAV hash. Missing or stale measurements
disable the comparison without blocking playback or labeling.

**Pitch shape** shows the clip in green and the other clips' average in gray.
It covers the measured speech span in twelve time bins. Each clip's curve is
centered on its own median voiced pitch and expressed in semitones, so a high
voice register does not automatically look excited. Missing voiced evidence is
left blank; there is no interpolation across unvoiced bins. Beginning/ending
measurements use up to 850 ms of speech at each edge and require voiced evidence.
The normalized full shape can also trigger a warning when at least eight bins
have enough comparisons. Rising pitch is not a rule for a particular emotion;
questions, recording effects and acting choices can produce overlapping patterns.

Method context: [GeMAPS research paper](https://mediatum.ub.tum.de/doc/1523509/document.pdf)
and [voice quality and affect study](https://www.sciencedirect.com/science/article/pii/S0167639302000821).
This implementation is a local comparison screen, not a GeMAPS implementation
or a calibrated emotion classifier.

Rebuild measurements with `scripts/audit_book1_label_acoustics.py` in the local TTS
environment. The dated audit directory contains the saved review snapshot, full
measurement report and waveform feature cache. It does not replace the live review.

## Comparison mood buckets

Warned clips show **Closest moods to compare**, with up to three other moods
within the listener's selected voice family. Scalar pitch/pace/level and relative
pitch shape do not establish timbre identity, so they cannot suggest a different
voice family. Text and intended emotion never enter the ranking.

Suggestions require at least eight measured destination clips and evidence from
seven other clips in six other paragraphs, with measurements for at least half
the bucket. The target's entire paragraph is excluded from both comparisons.
Each destination must cover the same reliable metrics as the current bucket,
including at least four independent measurement groups. Related contour measures
count as one group. Scores use the current bucket's variation scales for every
destination, so a broadly mixed destination cannot win merely by being variable.
For the **Stronger measured match** label, the combined distance must be at least
30% lower than the current bucket's, with
no single distance above three scales and fewer than two above 2.5. At least one
warning cause must improve by 30%. These are comparison rules, not calibrated
emotion probabilities. Inconclusive cases still show the nearest comparison
buckets, explicitly marked as uncertain or no closer than the current bucket.
This gives the listener examples without claiming the current label is wrong.
Sparse or incompatible buckets are omitted. When available, the panel also offers
a current-bucket example from another paragraph without an audio warning.

Each hint includes its comparison values, an actual example from another paragraph,
and a link to open the bucket in a new tab. **Use these choices** only stages the
dropdown values. **Move clip** remains necessary to save; hints never approve or
change labels automatically. Example playback does not credit listening to the
target clip. Hints update after moves, undo, saved label changes, and refresh.

## Training gate

A confirmation records voice family, mood, and the exact WAV SHA-256. Changing any of these
invalidates it. To require Step 2 before exporting:

```powershell
training-work/.venv-tts/Scripts/python.exe scripts/finalize_book1_training_review.py --all --require-bucket-review --output <new-directory>
```

The default exporter still permits reviewed clips without bucket confirmation and reports
how many have bucket confirmations. Full source, recording, precision, WAV integrity, and overlap
checks continue to run during export. Review counts are not a promise that all clips pass export.
The labels prepare the corpus; this change does not implement a mood-conditioned Higgs trainer.
