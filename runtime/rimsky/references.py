from __future__ import annotations

import math
from pathlib import Path

from .util import AppError


def inspect_reference(path: Path) -> dict:
    import numpy as np
    import soundfile as sf

    try:
        with sf.SoundFile(path) as audio:
            rate, channels, expected = audio.samplerate, audio.channels, audio.frames
            if not 8000 <= rate <= 192000 or not 1 <= channels <= 2 or expected <= 0:
                raise AppError('Voice reference needs nonempty mono or stereo audio at a supported sample rate.')
            total = 0
            peak = 0.0
            energy = 0.0
            for block in audio.blocks(blocksize=65536, dtype='float32', always_2d=True):
                if not np.isfinite(block).all():
                    raise AppError('Voice reference contains invalid samples.')
                total += len(block)
                peak = max(peak, float(np.max(np.abs(block))))
                energy += float(np.sum(np.square(block, dtype=np.float64)))
            if total != expected:
                raise AppError('Voice reference audio is truncated.')
            if peak < 0.00001:
                raise AppError('Voice reference is silent.')
            return {'sample_rate': rate, 'channels': channels, 'samples': total,
                    'duration': total / rate, 'peak': peak,
                    'rms': math.sqrt(energy / (total * channels))}
    except (OSError, RuntimeError, ValueError) as error:
        raise AppError('Voice reference cannot be decoded. Use a valid local WAV, FLAC or MP3 recording.') from error
