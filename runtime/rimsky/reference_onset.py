"""Reference onset padding: a reviewed reference clip that starts or ends right
on speech makes Higgs start takes abruptly (clipped first sound).

Measured 2026-10-16: all 151 clipped first takes of two Chapter 2-3 runs came
from one narrator reference whose speech starts after 13 ms; with 0.15 s of
digital silence at both ends, 60 test sentences went from 30 clipped starts to
1, and from 46 to 54 word-exact (same plans and seeds). The derived copy keeps
the reviewed voice and words; the synthesis plan keeps the original reference
identity (sha256) and records the derived audio and its provenance.
"""
from __future__ import annotations
import json
from pathlib import Path

POLICY = 'reference-onset-padding-v1'
PAD_SECONDS = 0.15
MIN_EDGE_SECONDS = 0.10
THRESHOLD = 0.01


def _edges(audio, rate):
    import numpy as np
    mono = np.abs(audio if audio.ndim == 1 else audio.mean(axis=1))
    active = np.flatnonzero(mono > THRESHOLD)
    if not len(active):return 0.0, 0.0
    return active[0] / rate, (len(mono) - 1 - active[-1]) / rate


def padded(reference: dict, root: Path, folder: Path) -> dict:
    """The reference with derived padded audio when its lead or tail silence is too short."""
    import numpy as np
    import soundfile as sf
    from .util import file_digest
    source = root / reference['audio']
    audio, rate = sf.read(source, dtype='float32')
    if file_digest(source) != reference['sha256']:raise ValueError('Reviewed reference changed: ' + str(source))
    lead, tail = _edges(audio, rate)
    if lead >= MIN_EDGE_SECONDS and tail >= MIN_EDGE_SECONDS:return reference
    target = folder / f"{reference['sha256'][:16]}-onset{int(PAD_SECONDS*1000)}ms.wav"
    if not target.exists():
        folder.mkdir(parents=True, exist_ok=True)
        pad = np.zeros((round(PAD_SECONDS*rate),) + audio.shape[1:], dtype='float32')
        sf.write(target, np.concatenate([pad, audio, pad]), rate, subtype='PCM_16')
        target.with_suffix('.json').write_text(json.dumps({'policy': POLICY, 'derived_from_sha256': reference['sha256'],
            'pad_seconds': PAD_SECONDS, 'original_lead_seconds': round(lead, 3), 'original_tail_seconds': round(tail, 3),
            'audio_sha256': file_digest(target), 'voice_and_words_unchanged': True}, indent=1), 'utf-8')
    return {**reference, 'audio': target.resolve().relative_to(root.resolve()).as_posix(),
            'onset_padding': {'policy': POLICY, 'source_audio': reference['audio'], 'source_sha256': reference['sha256'],
                              'derived_sha256': file_digest(target), 'pad_seconds': PAD_SECONDS,
                              'original_lead_seconds': round(lead, 3), 'original_tail_seconds': round(tail, 3)}}
