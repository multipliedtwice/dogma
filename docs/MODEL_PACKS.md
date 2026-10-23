# Local model and voice packs

A pack is a local, immutable directory. No runtime path in the manifest is a model-hub identifier or download URL.

```text
my-pack/
  pack.json
  models/text/model.gguf
  models/speech/
  voices/
  LICENSES/
```

Start from `packs/pack.example.json`, copy it as `pack.json`, and supply the actual files before sealing. The example uses Qwen stock speaker names as a technical evaluation configuration; it is not a narrator imitation.

## Text model

The adapter uses llama-cpp-python with a local GGUF and JSON-schema-constrained responses. The initial candidate is Qwen3-4B-Instruct-2507. Choose and record a compatible conversion and quantization; none is supplied or represented as verified here. `text_model.context` controls the configured context window; it defaults to 8192 tokens. A pack that declares less than 8192 cannot be selected, sealed, release-checked or used for new analysis (`Text model context must be at least 8192 tokens.`). Selection has one exception: a pack with 4096 to 8191 tokens whose fingerprint an existing generation is pinned to, for example after its folder moved, can be selected again so that those generations resume and repair. The health check then still reports `Text model context must be at least 8192 tokens.`, and new generations stay blocked. Generations already pinned to a pack with 4096 to 8191 tokens keep rendering, resuming, repairing, cast review and corrections; an unfinished analysis on such a pack is refused with the same message. Playback and export do not load the pack. The reply budget of an analysis request is its output limit: twice the estimated output or 3,072 tokens, whichever is larger. Prompt short of room: registry entries lose descriptions first (listed compactly as ID, name, ≤3 aliases; still valid IDs), then least relevant entries dropped. Multi-passage batch keeps characters named in it or its context and accepted context speakers listed; split when those alone leave less room than the limit or when a reply reaches the budget. A single passage that lacks room for the limit even without registry entries is sent without them and gets the remaining room as its budget; a passage whose remaining room is smaller than its estimated output, or whose reply reaches its budget, is narrated by the narrator voice and listed as degraded, and no source text is dropped. An alias pair that exceeds the context or output limit stays unmerged with a casting notice. Casting runs at most 256 alias comparisons; pairs beyond that stay unmerged with a separate notice.

Structured output constrains syntax, not literary understanding. Evaluate Russian dialogue, surnames/patronymics/diminutives, attribution after speech, interrupted dialogue, and returning characters before selecting a release pack.

## Speech models

`speech.variant = custom` calls `generate_custom_voice`. Use the exact supported speaker names of the selected checkpoint. Set `instruction_control = true` only for a checkpoint whose instruction behavior is actually supported and has been evaluated, such as the documented 1.7B CustomVoice route.

`speech.variant = clone` calls `generate_voice_clone` with locally generated reusable conditioning prompts. Each profile needs `references.neutral.audio` and its exact transcript. Add references named after supported application deliveries: `soft`, `whisper`, `firm`, `excited`, `sad`, `angry`, `questioning`.

The Base cloning method is not treated as if it accepted CustomVoice instructions. A missing reference variant uses neutral and records a passage warning. A CustomVoice pack with `instruction_control = false` renders every passage neutrally; instead of a warning on each passage, the generation shows one `delivery_not_supported` notice with the number of expressive passages. The audio reuse key records the delivery actually applied. The identity and expressiveness of those variants still need listening evaluation.

`packs/clone-voice.example.json` shows only the shape of one reference-based profile. Its transcript placeholders must be replaced with the exact text of real, authorized reference recordings. Exactly one narrator and at least one character profile are required as configuration roles (see below). They can belong to the same narrator and share a base speaker or recording. The repertoire needs enough distinguishable performances for the people taking part in a local conversation, not one unique voice per person in the book.

A profile can declare `gender` (`female` or `male`) and `age_bands` (distinct values from `child`, `teen`, `adult`, `elderly`). Both are optional; profiles without them stay compatible with every character but rank below a profile whose declared value matches and above one whose declared value conflicts, so unedited existing packs keep their fingerprints. Adding these fields edits the manifest and creates a new pack revision; generations pinned to the earlier revision do not accept it. Casting prefers gender-compatible profiles and uses age bands as a preference only. When no character profile declares `gender`, or none declares `age_bands`, casting shows a warning. `packs/pack.example.json` declares `gender` for its character profiles from the Qwen3-TTS speaker descriptions (Aiden: male, Serena: female). It declares no `age_bands`, so its casting shows the age-band warning. When some profile of a character's pool declares `gender`, casting lists speaking characters with unknown or disputed gender, and characters with a known gender that received a profile without `gender`, as casting notices.

Profile IDs must remain unique. Shared underlying CustomVoice speakers and identical neutral clone recordings are permitted, including narrator/character profile reuse. For local casting conflicts, identity follows the speech variant: CustomVoice profiles selecting the same case-insensitive speaker, or clone profiles with a checksum-identical neutral recording, count as one baseline performance. A speaker label on a clone profile is not used for identity; renaming a profile or reference file does not create another distinguishable voice. No character profile is reserved for a particular character. Non-interacting characters share a performance when it fits them better; among equally fitting performances, casting prefers the one with the least assigned speech. Actual audible distinction and expressive reference variants still need listening evaluation.

## Voice roles, families and groups

Every profile has a `role`:

- `narrator`: exactly one per pack; it reads narration.
- `narrator_variant`: an alternative narration performance, for example a softer reading for historical prose. It is never cast to a character.
- `mechanical`: cast only to characters that analysis marks as mechanical, such as robots, ship computers or announcement systems. When a pack has no mechanical profile, those characters use character profiles and casting shows a notice.
- `character`, the default when `role` is absent: cast to people and to collective speakers such as a crowd.

Existing sealed packs keep their declared roles and fingerprints, but a voice declared `character` (or with no role) whose ID is `mechanical_game_voice` or `mechanical_stats`, or that is tagged `game_ui`, is treated as mechanical and never enters the ordinary-person pool. A `character` voice whose ID, family, group or tags mention "mechanical" is ambiguous: it is held out of both pools and the pack reports a warning until its role is declared. Newly authored packs must declare `mechanical` explicitly.

An optional `presentation_voices` profile (schema 1) maps presentation channels (`game_ui`, `radio`, `telepathic`, `written_text`, …) and content registers to voices: `speaker` keeps the cast voice, `fixed` names one voice, `mapped` maps the assigned voice, prefix or tag. A rule applies only to a part whose context cites evidence with high or medium confidence. A provisional, ambiguous, missing or unmapped rule never becomes an accepted warning: the passage is held (`presentation_review`) until an explicit verdict confirms it.

Analysis provider and context floor: `text_model.analysis_mode` is `staged` (default; separate structure, speaker and context calls with a two-pass local identity check), `legacy` (one schema-constrained call per batch; every local identity stays a review item), `auto`, `codex_cli` or `claude_cli`; `dialogue_style` is `auto` or `russian_dash`. The application setting `character_analysis` (`auto` resolves once at generation creation: Codex CLI, then Claude Code CLI, then local) is pinned in the generation config. The 8192-token context floor applies to new local analysis, including local secondary passes; CLI analysis is not rejected for the local model's context size. Pinned generations keep their legacy pack policy.

`family` names the performer family: letters, digits, `.`, `_` or `-`, at most 64 characters; it defaults to the profile ID. Profiles that share a family, a CustomVoice speaker, or a checksum-identical clone neutral recording form one performance identity, and that identity also covers the narrator. Give a narrator variant recorded by the narrator the narrator's family. `group` is an optional display label of at most 64 characters, such as `Masculine voices`; the cast shows it, and casting does not use it. Each cast entry records the role, family and group of its profile. During cast review, a character can get another profile from its pool, and a narrator or narrator-variant profile can be chosen for the whole book or for one chapter.

A character receives a profile that shares the narrator's identity only when local collision avoidance, gender compatibility or a possible alias of the same person leaves no better profile, and casting then lists that character in a notice. Single-speaker packs, where every character profile shares the narrator's speaker, therefore list every speaking character. A speaking character with unknown or disputed gender gets a gender group from the known-gender speech volume of the book: unknown speakers are assigned, largest first, to the group that keeps their combined speech closest to that ratio. Local collision avoidance still ranks first; among the profiles of that group, and profiles without `gender`, the usual age, tag and least-assigned-speech order applies.

`packs/pack.example.json` shows each role: the narrator, a narrator variant on another stock speaker with its own family, a mechanical profile, and character profiles with `gender`, `family` and `group`. Adding or changing these fields edits the manifest, so it needs a new seal and creates a new pack revision.

One neutral reference is not documented proof that the app can reproduce a professional narrator's entire acting range. Do not name an evaluation pack after a real narrator unless the intended assets and permissions have actually been obtained.

## Optional aligner

Add an `aligner` object with a local `path` pointing to a Qwen3-ForcedAligner-0.6B directory. Package the aligner in a separate environment. The current Qwen TTS and ASR distributions pin different Transformers versions; combining their requirements in one environment is not the intended installation.

One isolated aligner process is reused for the selected generation rather than loading weights for every passage. Cues must cover the spoken text and have valid ordered timestamps. Invalid cues retain passage subtitles with a warning.

Without the optional aligner, passage-level subtitles remain available. Word timestamps are derived from generated audio plus the actual spoken text, not invented from character counts.

## Sealing

After every asset and licence file is present:

```sh
python scripts/seal_pack.py /absolute/path/to/my-pack
```

The command hashes local files, validates the resulting manifest, and does not download anything. Changes require a new seal and therefore a new pack fingerprint. It rejects symlinks for distributable packs. Only the root `pack.json` is excluded from asset hashes; nested files with that name are ordinary protected assets. Keep multiple pack revisions in different directories when old generations need to remain resumable. Preparation compares the size, modification time, change time (`st_ctime`) and file ID (`st_ino`) of every sealed file with the values seen at verification before it starts the inference process, immediately before and after every request to the inference process, after synthesis before its audio is published, and immediately before and after every alignment request. It also compares the set of files and symbolic links in the pack folder, ignoring operating-system metadata such as `.DS_Store`, so an added file or link counts as a change, and so does a sealed path that now leads outside the pack. It stops with `Model pack files changed after verification. Select the pack again in Settings.` when anything differs. On Windows `st_ctime` is the file creation time, so a same-size rewrite that restores the modification time is not detected there. A reply received while the files changed is discarded. Verification results are cached by file signature, so a verified pack folder moved on the same disk without changing its files is not hashed again.

`rights.voice_assets` records the actual basis on which the chosen assets can be redistributed. The release guard checks that records and licence files exist; it cannot determine whether a legal representation is correct.

Model-hub cache/symlink layouts should be converted into ordinary files for a distributable pack. Include the full tokenizer and model configuration alongside weights. PyTorch and llama.cpp runtimes are packaged separately from the pack.


## Reviewed narrator adaptations

The maintainer pipeline in [TRAINING.md](TRAINING.md) can publish a new immutable pack from an explicitly reviewed output of the upstream single-speaker Base trainer. Such a checkpoint is loaded as CustomVoice, not Base cloning, and instruction control remains false because the inspected trainer does not teach delivery instructions. The two required application roles may share its one speaker; that is not evidence of distinct trained character performances, and casting lists every speaking character as sharing the narrator's performance. Private trained packs remain selectable locally but fail distribution admission without recorded redistribution authorization. Training and publication never change an existing pack or saved generation.

## Trained-model language conditioning (0.1.16)

`speech.language_mode` accepts `book` (the default for existing packs) or `auto`. New packs published by the audited single-speaker Qwen trainer use `auto` to match its codec prefix. The worker passes `Auto` to Qwen for these packs; this is not a text translation or an accuracy guarantee. The mode is part of the immutable pack fingerprint. Use a matching worker; older source versions do not implement this field. Existing saved generations and packs are not changed automatically.
