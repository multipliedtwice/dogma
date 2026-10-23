"""Duration-ordered Qwen3-ASR batches shared by the primary audit and its same-weights witnesses.

Batch 2 in file order spent 103 s on a 124-take section; duration-ordered
batches of 16 took 17.9 s with one orthographic difference (жареные /
жаренные), and 32 with last_position_logits 15.5 s for 149 takes at 4.4 GB
(iteration 7 measurements, 2026-10-17). Similar lengths keep padding small;
an audio-seconds budget bounds memory for long sentences. An out-of-memory batch is split in half rather than failing the audit.
Qwen3-ASR projects every prompt position onto its 152k vocabulary although
greedy generation reads only the last: last_position_logits removes that
(1374-token glossary prompts reserved 12 GB at batch 2 and spilled into
shared system memory on Windows).
Every recognizer config that uses this records its implementation hash.
"""
import sys
import types
import soundfile as sf

# qwen_asr imports nagisa, a Japanese tokenizer used only by its forced aligner (never
# called here): 2.8 s of every recognizer start. Import this module before qwen_asr.
if 'nagisa' not in sys.modules:
    _stub = types.ModuleType('nagisa')
    def _unavailable(*args, **kwargs):raise RuntimeError('nagisa is not loaded in the Russian word audit')
    _stub.tagging = _unavailable
    sys.modules['nagisa'] = _stub

BATCH = 32
MAX_SECONDS = 240
# Glossary prompts carry ~1,374 context tokens each: batch 4 peaked at 4.96 GB, batch 8 spilled.
CONTEXT_BATCH = 4


def last_position_logits(model):
    """Project only the final position onto the vocabulary; greedy outputs are unchanged."""
    heads = [m for name, m in model.model.named_modules() if name.endswith('lm_head')]
    if len(heads) != 1:raise ValueError('Expected one Qwen3-ASR lm_head')
    if not getattr(heads[0], '_last_position_only', False):
        heads[0].register_forward_pre_hook(lambda module, args: (args[0][:, -1:, :],) + args[1:])
        heads[0]._last_position_only = True
    return model


def transcribe(model, items, audio_of, size=BATCH, max_seconds=MAX_SECONDS, **kwargs):
    """Yield (item, result) for every item; kwargs reach model.transcribe (context lists are per item)."""
    import torch
    context = kwargs.pop('context', None)
    seconds = [sf.info(str(audio_of(item))).duration for item in items]
    batches = []
    for i in sorted(range(len(items)), key=seconds.__getitem__):
        if not batches or len(batches[-1]) >= size or sum(seconds[j] for j in batches[-1]) + seconds[i] > max_seconds:batches.append([])
        batches[-1].append(i)
    stack = batches[::-1]
    while stack:
        batch = stack.pop()
        extra = {**kwargs, **({'context': [context[i] for i in batch]} if context is not None else {})}
        try:heard = model.transcribe([str(audio_of(items[i])) for i in batch], **extra)
        except torch.cuda.OutOfMemoryError:
            if len(batch) == 1:raise
            torch.cuda.empty_cache(); half = len(batch)//2
            stack += [batch[half:], batch[:half]]; continue
        if len(heard) != len(batch):raise ValueError('Incomplete transcription')
        for i, result in zip(batch, heard):yield items[i], result
