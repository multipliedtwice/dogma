"""Synthetic CLI evidence for preprocessing tests; never invokes a provider."""
import json
from pathlib import Path
from rimsky.util import file_digest,write_json


def evidence(path,system,request,schema,answer):
    path=Path(path);part=path/'attempt-0';part.mkdir(parents=True,exist_ok=True)
    write_json(part/'request.json',{'system':system,'request':request,'schema':schema,'model':'gpt-6-luna',
        'reasoning_effort':'low','max_attempts':1,'command':['fixture-only']})
    write_json(part/'raw-answer.json',answer);write_json(part/'result.json',{'returncode':0,'output_exists':True})
    (part/'stdout.txt').write_text('',encoding='utf8');(part/'stderr.txt').write_text('',encoding='utf8')
    write_json(path/'manifest.json',{'max_attempts':1,'files':{p.relative_to(path).as_posix():file_digest(p) for p in part.iterdir()}})
    return {'cli_evidence':str(path),'cli_evidence_sha256':file_digest(path/'manifest.json')}


def metadata_evidence(metadata,path):
    return {**metadata,**evidence(path,'fixture',{'items':[]},{},{'decisions':{}})}


def responder(answer,metadata):
    def call(system,request,schema,*args,evidence_dir=None,**kwargs):
        raw=answer(system,request,schema) if callable(answer) else answer
        if isinstance(raw.get('decisions'),list):
            raw={'decisions':{d['id']:{'choice':d.get('stressed',d.get('word')),'basis':d['basis']} for d in raw['decisions']}}
        raw={'decisions':{key:{**decision,**({'confidence':decision.get('confidence',80)} if request.get('pass_id')=='pass-1' else {})}
                          for key,decision in raw['decisions'].items()}}
        proof=evidence(evidence_dir,system,request,schema,raw) if evidence_dir is not None else {}
        return raw,{**metadata,**proof}
    return call
