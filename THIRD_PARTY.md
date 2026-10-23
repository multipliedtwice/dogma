# Dependency provenance and release obligations

No model weights, narrator recordings, third-party binary runtimes, or fonts are distributed in this source archive. Dependencies are declared in the package/requirements manifests. This file identifies primary references, not a substitute for including the exact dependency licence texts in a binary release.

| Component | Primary reference | Use |
|---|---|---|
| Tauri | https://v2.tauri.app/develop/sidecar/ | Desktop shell and bundled runtime pattern |
| React | https://react.dev/ | Interface |
| shadcn/ui | https://ui.shadcn.com/docs/installation/vite | Local component organization/style |
| Radix | https://www.radix-ui.com/primitives | Dialog and slot primitives |
| llama-cpp-python | https://github.com/abetlen/llama-cpp-python | Local GGUF instruction inference |
| Qwen3-TTS | https://github.com/QwenLM/Qwen3-TTS | Local speech and voice prompts |
| Qwen3-TTS package definition | https://raw.githubusercontent.com/QwenLM/Qwen3-TTS/main/pyproject.toml | Separate dependency compatibility |
| Qwen3-ASR / ForcedAligner | https://github.com/QwenLM/Qwen3-ASR | Optional saved-audio alignment |
| Qwen3-ASR package definition | https://raw.githubusercontent.com/QwenLM/Qwen3-ASR/main/pyproject.toml | Separate aligner environment |
| Qwen text candidate | https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507 | Initial evaluation candidate, not bundled |
| espeak-ng (espeak-phonemizer-windows) | https://github.com/mush42/espeak-phonemizer-windows | Russian grapheme-to-phoneme comparison for recognizer-orthography checks; GPL-3.0, verification tooling only |
| pymorphy3 + pymorphy3-dicts-ru | https://github.com/no-plagiarism/pymorphy3 | Dictionary-word test for homophone checks and the book glossary; MIT code, OpenCorpora-derived dictionary |
| wordfreq | https://github.com/rspeer/wordfreq | Word frequency for choosing long-tail words that receive stress marks (stress-rare-v1); Apache-2.0 code, CC BY-SA 4.0 data |
| FFmpeg | https://ffmpeg.org/documentation.html | Audio assembly and encoding |
| lxml | https://lxml.de/ | Bounded XML/HTML parsing |
| Pillow | https://pillow.readthedocs.io/ | Decode/re-encode images |
| certifi | https://github.com/certifi/python-certifi | Mozilla CA certificates added to the system trust store for optional Image API requests; MPL-2.0 |
| DOMPurify | https://github.com/cure53/DOMPurify | Reader-side rich-markup sanitation |
| OpenAI image API | https://developers.openai.com/api/docs/guides/image-generation | Optional consent-based online images |
| YuE2-3B (m-a-p), audio.cpp GGUF | https://huggingface.co/audio-cpp/Yue2-3B-GGUF | Optional generated background music; weights and generated music are CC BY-NC 4.0 (non-commercial, attribution required) |
| audio.cpp | https://huggingface.co/audio-cpp | Local YuE2 music and Higgs speech runner |
| Dog Snarl Grunt Grumble, qubodup | https://opengameart.org/content/dog-snarl-grunt-grumble | CC0-1.0; pinned dog growl for explicit animal vocalizations, separate from speech synthesis |

The application source does not incorporate BookNLP, VoxNovel, S-VoCAL, Foliate-js, Laya, or mpv code. Those were discussed in planning; do not mistake them for installed components. No performance result from another project is represented as a result for this implementation.

A binary distributor must include the applicable notices and obligations of the exact model, quantization/conversion, Python packages, native libraries, audio encoders, and voice assets used. FFmpeg build configurations can differ. The pack's written rights record is an operator-supplied statement, not an automated legal determination.

A packaged core runtime includes `certifi` (pinned to 2026.7.22 in `runtime/requirements.txt`), which is licensed under the Mozilla Public License 2.0. Under that licence, a binary release that includes it carries the licence text and tells recipients how to obtain the package's source form.

The custom column-capital SVG and PNG/ICO/ICNS conversions are application assets created for this implementation. They do not depend on a bundled font.

The dependency manifest and lockfile pin DOMPurify to the published 3.4.15 release (https://github.com/cure53/DOMPurify/releases/tag/3.4.15). Its integration is covered by the successful frontend production build; no dependency binary is vendored in the source archive.
