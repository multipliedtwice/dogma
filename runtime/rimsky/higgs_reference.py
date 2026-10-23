"""Reference-audio QC, separate from protected human voice/mood decisions."""
from pathlib import Path
from .audio_join import boundary_check, JOIN_POLICY
from .util import file_digest, SpeechHoldError


def check_reference(path, expected_hash):
    import numpy as np
    import soundfile as sf
    path=Path(path)
    if file_digest(path)!=expected_hash:
        raise SpeechHoldError('Reviewed Higgs reference bytes changed.')
    audio,rate=sf.read(path,dtype='float32',always_2d=True)
    if rate!=24000 or audio.shape[1]!=1 or not len(audio) or not np.isfinite(audio).all():
        raise SpeechHoldError('Higgs reference must be finite mono 24 kHz audio.')
    try:edges=boundary_check(audio[:,0],rate)
    except ValueError as exc:raise SpeechHoldError('Higgs reference contains no usable speech signal.') from exc
    if edges['unsafe_start'] or edges['unsafe_end']:
        raise SpeechHoldError('Higgs reference has an active audio edge; choose a complete reviewed reference, never fade the speech.')
    return {'audio_sha256':expected_hash,'seconds':len(audio)/rate,'edges':edges,
            'boundary_policy':JOIN_POLICY,'listening_approval':False}
