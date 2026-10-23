"""Independent audio-only no-vocals check; expected music description is not sent."""
import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import digest,file_digest,write_json
from rimsky.speech_quality import word_tokens


def verify(audio,output):
    import torch
    from qwen_asr import Qwen3ASRModel
    torch.set_num_threads(4)
    model_path=ROOT/'training-work/models/Qwen3-ASR-1.7B'
    files={p.relative_to(model_path).as_posix():file_digest(p) for p in model_path.rglob('*') if p.is_file() and '.cache' not in p.parts}
    signature={'backend':'qwen3_asr','model_files':files,'dtype':'bfloat16','device':'cuda:0',
               'language':'Auto','implementation_sha256':file_digest(Path(__file__))}
    before=file_digest(audio)
    model=Qwen3ASRModel.from_pretrained(str(model_path),device_map='cuda:0',dtype=torch.bfloat16,
        attn_implementation='sdpa',local_files_only=True,max_new_tokens=512)
    result=model.transcribe(str(audio),language=None)
    if len(result)!=1 or file_digest(audio)!=before:raise ValueError('Music changed during independent check')
    heard=result[0].text
    write_json(output,{'audio_sha256':before,'verifier':signature,'verifier_sha256':digest(signature),
                       'heard':heard,'heard_words':word_tokens(heard)})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--audio',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();verify(a.audio.resolve(),a.output.resolve())
