from __future__ import annotations

import contextlib
import json
import os
import sys

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['DO_NOT_TRACK'] = '1'

model = None
model_key = None


def align(request):
    global model, model_key
    import torch
    from qwen_asr import Qwen3ForcedAligner

    device = request.get('device', 'cpu')
    if device == 'auto':
        device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
    if device == 'cuda':
        device = 'cuda:0'
    key = (request['model'], device)
    if model is None:
        model = Qwen3ForcedAligner.from_pretrained(
            request['model'], device_map=device,
            dtype=torch.float32 if device in ('cpu', 'mps') else torch.float16,
            local_files_only=True,
        )
        model_key = key
    if key != model_key:
        raise ValueError('Alignment model changed during a pinned generation.')
    results = model.align(audio=request['audio'], text=request['text'], language=request['language'])
    return [{'text': item.text, 'start': float(item.start_time), 'end': float(item.end_time)} for item in results[0]]


def main():
    for handle, errors in ((sys.stdin, 'strict'), (sys.stdout, 'replace')):
        if hasattr(handle, 'reconfigure'):
            handle.reconfigure(encoding='utf-8', errors=errors)
    stream = sys.stdout
    for line in sys.stdin:
        request = {}
        try:
            if len(line) > 4 * 1024 * 1024:
                raise ValueError('Alignment request is too large.')
            request = json.loads(line)
            if request.get('method') != 'align':
                raise ValueError('Unknown alignment operation.')
            with contextlib.redirect_stdout(sys.stderr):
                result = align(request['data'])
            response = {'id': request['id'], 'result': result}
        except Exception as error:
            response = {'id': request.get('id'), 'error': f'{type(error).__name__}: {str(error)[:2000]}'}
        stream.write(json.dumps(response, ensure_ascii=False) + '\n')
        stream.flush()


if __name__ == '__main__':
    main()
