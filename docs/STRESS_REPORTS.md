# Narration feedback and later correction

When the listener selects one Russian word in the production reader and opens its context menu, the reader lists every possible vowel position for stress. Choosing one saves that exact intended form. The same panel accepts a category and up to 2,000 characters of free text for pronunciation, pacing, voice/intonation, or another issue.

A report is local to the book library. It stores the selected text, intended stress when applicable, source passage and offset, generated segment, generation ID, audio hash, spoken-text snapshot, and playback offset when that segment is playing. Reporting does not pause playback, change the EPUB, or call a language model. For an external Higgs listening generation, an explicit stress choice can immediately queue source-bound retakes. Reports are listed under Book tools → Narration feedback.

The report remains after its generation is removed so the listener's observation is not lost. If the original audio was deleted, its hash and position remain provenance, not a playable asset.

If the word uniquely matches a known character name or alias, the correction is also saved in a cumulative series registry independent of any one imported volume. The current generation queues that character's ready occurrences in listening order (reported point, forward, then already heard), while later volumes inherit the reviewed pronunciation and add their new characters. Ambiguous names and non-character words remain occurrence-only. Release builds may ship explicitly exported, versioned seeds from books reviewed during development; an installation's local overlay takes precedence.

Correction flow after a listening session:

1. Classify each report from its source, prepared text, audio, recognizer evidence, and take history. A selected stress records listener intent but does not by itself prove which pipeline stage failed.
2. Confirm the intended stress by hearing the source and a corrected TTS probe. A report alone is an observation, not a pronunciation rule.
3. Choose the narrowest mitigation: retake when Higgs ignored a correct mark; repair the contextual decision, word-form dictionary, or name-family propagation when preparation was wrong; or adjust recognizer equivalence when the audio was already correct. Homographs with different meanings must not be changed globally by spelling alone.
4. Create a new saved generation through the existing revision path. Keep the old generation and its audio. Regenerate only affected segments and reuse unaffected audio.
5. Check corrected stress and naturalness by listening, then switch playback to the new version at the corresponding segment and nearest valid offset.

The Book 1 listening generation is labelled provisional. Its character references still need listening review, and it does not yet reproduce the narrator's distinct radio, public-address, and game-interface processing. Stress reports help review pronunciation in this draft; they do not certify voice identity or performance quality.

## Spoken respelling (listener request, 2026-10-21)

Higgs reads a plain е after a consonant as soft, so a hard reading (Мозэс, like English "tech" in тэк-купол) needs э in the synthesis text. A *spoken respelling* may differ from the printed word only by е→э (or Е→Э) and е→ё at explicit positions, plus stress marks; ё is always stressed and is its own mark. `runtime/rimsky/respelling.py` (`validate_respelling`, `validate_respelled_stress`) is the single validator; the printed text never changes.

- **Report panel:** next to the click-to-stress choices, the listener can type the whole word with its stress and substitutions (`Мо́зэс`). The runtime accepts it only when every other letter is unchanged and exactly one stress is marked. When the word matches one character, the series registry records the respelled stressed name, and every inflected form that keeps the letter inherits it (Мозеса → Мо́зэса).
- **Character names:** a stressed name may carry respellings (`validate_name_stress`); `name_stress.name_spans` carries them into inflected forms. Listener respelling for an already frozen generation is a sidecar document applied with `respelling.apply_listener_names` (the model answer is kept as `model_stressed`).
- **Series terms (non-characters):** `resources/series-terms.json` holds listener-authoritative, versioned term respellings per series (plus an optional local overlay `series-terms.json` beside the library). Reality Benders has `тек-купол → тэк-купол`; it matches the compound with any dictionary inflection of the head (тек-куполом, тек-купола), never текст or a bare тек. The frozen lexicon is stored in the generation's pronunciation spec as `terms`.
- **Synthesis:** `respelling.respell_text` runs before the stress passes, so the respelling enters the synthesis text and key, and each unit records `synthesis.listener_respelling` (printed/spoken pairs).
- **Recognition:** the recognizer never sees the text and writes either spelling. For recorded respellings only, `name_tolerance.tolerance(..., respellings)` accepts the heard printed or spoken token as `listener-respelling-v1`, a disclosed tolerated deviation with the raw transcript retained. Any other difference stays a mismatch.
