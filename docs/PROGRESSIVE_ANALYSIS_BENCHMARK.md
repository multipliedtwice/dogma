# Progressive chapter analysis benchmark

## Question

Can chapters farther ahead of the playback cursor receive progressively deeper
analysis, up to five or six passes, and thereby reduce manual speaker labeling?

All runs used the frozen speaker gold set. Provider requests never contained gold
answers. Evaluation outputs live under
`training-work/speaker-resolution-progressive-*`. No corpus review decision was
written or changed by these experiments.

## What did not work

Repeated voting and generic adjudication did not provide a reliable gain.

- On the 104-span policy-selection partition, the original Codex pass scored
  96/104. A Claude continuity audit happened to reach 102/104, but asking Codex
  to adjudicate those changes returned to 95/104.
- A consensus variant expanded coverage while reducing precision: its final result
  was 95/104.
- A joint-chapter policy reached 104/104 on that selection partition, but failed to
  generalize. On the untouched 912-span test partition, the original Codex result
  was 896/912, while the six-pass result was 895/912.
- Thirteen initial errors were shared by Codex and Claude. Agreement alone cannot
  detect shared errors.

Therefore the number of passes, provider agreement, and higher reasoning effort
are not confidence measures by themselves.

## What helped

A later Codex pass with the complete chapter supplied as one coherent context did
help on a position-selected late-book sample. This was an exploratory follow-up
after the untouched test had already been opened, so it needs prospective
validation on another book.

The sample contains every modeled span in contiguous final chapters c0038, c0039
and c0040: 162 spans selected by book position, not prediction correctness.

| Analysis | Correct | Accuracy | Changes from primary |
| --- | ---: | ---: | --- |
| Initial Codex, local context | 154/162 | 95.06% | baseline |
| Initial Claude, local context | 152/162 | 93.83% | — |
| Fresh Claude, complete chapter | 153/162 | 94.44% | fixed 4, introduced 5, changed 1 wrong answer to another wrong answer |
| Fresh Codex, complete chapter | **159/162** | **98.15%** | **fixed 5, introduced 0** |

The ten spans on which any of the four proposals differed were then sent through a
focused high-effort Codex pass. The normal-effort complete-chapter Codex answers
were 10/10 correct. The high-effort pass was 8/10 and changed two correct character
answers back to `unknown`. High effort is therefore not the default for this task.

The successful chapter pass produced ten answers whose exact evidence quote failed
the source validator. A separate locked-identity repair pass fixed all ten quotes
without changing a speaker. This is a useful later pass because its responsibility
is narrow and mechanically verifiable.

## Candidate schedule for prospective validation

1. **Structure:** exact source spans, dialogue turns, quotations and deterministic
   attributions.
2. **Initial identity:** Codex with the target and local textual context.
3. **Chapter consistency:** once a chapter is complete, Codex receives the compact
   whole chapter and resolves its turns together. Use low reasoning effort.
4. **Book continuity:** check recurring character identities and aliases against
   preceding chapters. This pass may propose changes but needs its own evaluation.
5. **Targeted challenge:** send only unresolved or conflicting cases to the
   alternate provider. Its answer is a warning/proposal, not an automatic override.
6. **Locked validation:** repair exact evidence and validate source ranges without
   reopening accepted speaker identities. Remaining conflicts stay held for review.

Chapters near playback need passes 1–3 before rendering. Chapters farther ahead can
receive passes 4–6 in the background. A pass is counted only when it has a distinct
job or materially broader context.

## Deployment gate

Do not enable automatic replacement from these exploratory results. Freeze the
schedule above before testing it prospectively on a later manually reviewed book.
Report accuracy after every pass, changes that fix errors, changes that introduce
errors, coverage, invalid evidence, and review holds. Production may adopt the
complete-chapter Codex pass only if the prospective result preserves the gain.

## Ollama comparison

The installed local models were tested with the same complete-chapter context.
Ollama output was isolated from corpus labels. Qwen was run over all 162 late-book
spans; the two larger models were too slow for that workload on this laptop and
were evaluated on the frozen ten disagreements where an extra pass would actually
be requested.

| Local model and policy | Scope | Result |
| --- | ---: | ---: |
| Qwen 3.5 4B, first chapter pass | 162 | 120/162 (74.07%) |
| Qwen 3.5 4B, anchored verification | 162 | 120/162 (74.07%) |
| Qwen agreement gate | 162 | 160 accepted at 75.00% precision |
| Bonsai 8B, first / anchored verification | hard 10 | 7/10 / 7/10 |
| Ministral 3 8B, first / anchored verification | hard 10 | 4/10 / 6/10 |
| Qwen independent unanchored pass | hard 10 | 3/10 |

Qwen changed only two answers during anchored verification; both remained wrong.
On the hard ten, its first and anchored passes scored 1/10. The independent pass
changed seven answers, fixed three, introduced one error, and changed three wrong
answers into other wrong answers. The three cases where the two Qwen passes agreed
were all wrong.

Bonsai's 7/10 came from returning `unknown` for all ten cases; seven gold answers
were `unknown`. Its second pass repeated every answer. Ministral's second pass fixed
two errors but its agreement gate accepted seven cases at only 57.14% precision.
Bonsai and Ministral agreed on three first-pass cases and those three were correct,
but this sample is too small and all three were conservative `unknown` decisions.

The local complete-chapter tests therefore do not change the provider order. Qwen
can propose labels for a free/private fallback, but every model-resolved local label
remains reviewable. Repeating one local model does not raise confidence. A local
cross-model agreement may prioritize the review queue only after a prospective,
larger evaluation demonstrates adequate precision.
