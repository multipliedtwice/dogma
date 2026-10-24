"""Decide ё for the ambiguous and unknown words of a book (text only; no audio, no GPU) for policy stress-rare-v4.

For every unit of the chosen generations' assignments the exact text the worker hands to auto_pronunciation is
rebuilt (reusing scripts/build_homograph_stress.py). rimsky.yo_restoration classifies each е word against the
pymorphy3 dictionary: unique ё words are restored at render time without a decision; ambiguous words (все/всё,
осел/осёл) all go to GPT-6 Luna at low effort, even when silero and RUAccent agree. Every unknown word that silero
stresses on е or RUAccent writes with ё also goes to this pinned adjudicator as sentence + the e and
ё candidates. It answers e | yo and echoes the word; letters can never change. Decisions bind to
sha256(unit text) + word + occurrence. Requests, per-batch attempts and answers are immutable.
--dry-run prints the class counts and the rows without calling a provider.
"""
import argparse, json, sys, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from rimsky.util import write_json, file_digest, digest
from rimsky import yo_restoration as yo
from build_homograph_stress import FOLDER, read, native_texts, lexicon
from rimsky import codex_analysis
from rimsky.cli_process import run_cli
import independent_luna_passes as independent

BATCH = 25
POLICY = 'yo-all-ambiguous-luna-change-check-v3'
PIN = ('codex_cli', 'gpt-6-luna', 'low')
DUAL_SYSTEM=yo.SYSTEM+(' For this transport, select the FULL candidate spelling in choice and return only the exact '
    'per-item decision object required by the schema, with basis. Do not emit legacy e/yo labels or extra word fields.')


def immutable(path,value):
    if path.exists():
        if read(path)!=value:raise ValueError('Immutable YO evidence changed: '+str(path))
    else:write_json(path,value)


def validate_provider(metadata):
    if any(metadata.get(k)!=v for k,v in zip(('provider','model','effort'),PIN)) or metadata.get('policy')!=POLICY:
        raise ValueError('YO requires pinned Luna-low adjudication without fallback')
    if (metadata.get('requested_model')!=PIN[1] or metadata.get('model_provenance')!='explicit-cli-model-selection'
            or not metadata.get('cli_version') or not Path(metadata.get('executable','')).is_absolute()
            or not re.fullmatch(r'[a-f0-9]{64}',metadata.get('executable_sha256',''))
            or metadata.get('structured_call_sha256')!=file_digest(Path(codex_analysis.__file__))):
        raise ValueError('Missing or changed isolated Luna CLI provenance')
    if file_digest(Path(metadata['executable']))!=metadata['executable_sha256']:
        raise ValueError('YO CLI executable changed')
    if not metadata.get('cli_evidence'):raise ValueError('Missing audited single-execution YO CLI evidence')
    if metadata.get('cli_evidence'):
        path=Path(metadata['cli_evidence'])
        if file_digest(path/'manifest.json')!=metadata.get('cli_evidence_sha256'):raise ValueError('YO raw CLI evidence changed')
        proof=read(path/'manifest.json')
        if proof.get('max_attempts')!=1:raise ValueError('YO CLI execution was not single-attempt')
        for relative,checksum in proof['files'].items():
            if not (path/relative).resolve().is_relative_to(path.resolve()) or file_digest(path/relative)!=checksum:raise ValueError('YO raw CLI artifact changed')


def call(system,body,contract,out,tag,*,evidence_dir=None):
    if evidence_dir is None:evidence_dir=out/'direct-calls'/tag
    executable=Path(codex_analysis.codex_executable())
    version=run_cli([str(executable),'--version'],timeout=20)
    if version.returncode or not version.stdout.strip():raise ValueError('Cannot pin Codex CLI version')
    metadata=dict(zip(('provider','model','effort'),PIN))|{'policy':POLICY,'requested_model':PIN[1],
        'model_provenance':'explicit-cli-model-selection','cli_version':version.stdout.strip(),
        'executable':str(executable),'executable_sha256':file_digest(executable),
        'structured_call_sha256':file_digest(Path(codex_analysis.__file__))}
    answer=codex_analysis.structured_call(system,body,contract,model=PIN[1],reasoning_effort=PIN[2],max_attempts=1,evidence_dir=evidence_dir)
    if evidence_dir is not None:metadata.update(cli_evidence=str(evidence_dir),cli_evidence_sha256=file_digest(evidence_dir/'manifest.json'))
    if Path(codex_analysis.codex_executable()).resolve()!=executable.resolve():raise ValueError('Codex executable selection changed during YO call')
    validate_provider(metadata)
    return answer,metadata


def validate_answer(answer,part):
    yo.validate(answer,part)
    if set(answer)!={'decisions'} or any(set(d)!={'id','choice','word','basis'} or not isinstance(d['basis'],str)
            or not d['basis'].strip() or len(d['basis'])>200 for d in answer['decisions']):
        raise ValueError('YO answer does not match the bounded schema')


def analyse_texts(texts, names, common, ruaccent=yo.ruaccent_yo, *, force_all=True):
    """(decisions, rows, stats) over {unit text -> chapter}; stats summed per class and per chapter."""
    from rimsky.auto_pronunciation import yo_witnesses
    decisions = []; rows = []; stats = {}
    for text, metadata in texts.items():
        chapter = metadata['chapter'] if isinstance(metadata, dict) else metadata
        spec = metadata.get('pronunciation') if isinstance(metadata, dict) else None
        local_names, local_common = names, common
        if spec:
            local_names = {cid: row['stressed'] for cid, row in spec['names'].items()}
            local_names.update(spec.get('proper_nouns', {}))
            local_common = spec.get('common', [])
        text, marked_e = yo.normalize_marked_e(text)
        spans, sil, dense = yo_witnesses(text, local_names, local_common, True)
        part = yo.analyze(text, spans, sil, dense, ruaccent(text),force_all=force_all)
        part['stats']['marked_e_normalized'] = len(marked_e['edits'])
        for r in part['rows']:r['chapter'] = chapter
        for d in part['decisions']:d['chapter'] = chapter
        decisions += part['decisions']; rows += part['rows']
        for k, v in part['stats'].items():stats[k] = stats.get(k, 0) + v
    return decisions, rows, stats


def dual_items(part):
    result=[]
    for i in part['items']:
        witnesses=i.get('upstream_witnesses',i.get('witnesses',{}));choice=witnesses.get('silero')
        existing=i['candidate_'+choice] if choice in ('e','yo') else None
        result.append({'id':i['id'],'sentence':i['sentence'],'word':i['word'],'candidates':[i['candidate_e'],i['candidate_yo']],
            'baseline':existing,'baseline_stage':'Silero YO witness; missing or noncategorical witness is unknown',
            **{k:i[k] for k in ('witnesses','upstream_witnesses','dictionary_forms') if k in i}})
    return result


def decoded_answer(part,selections):
    lookup={r['id']:r for r in selections};answer={'decisions':[]}
    for item in part['items']:
        vote=lookup[item['id']];word=vote['choice']
        answer['decisions'].append({'id':item['id'],'choice':'e' if word==item['candidate_e'] else 'yo',
                                   'word':word,'basis':vote['basis'][:200]})
    validate_answer(answer,part);return answer


def validate_batch(saved):
    part=saved['request'];folder=Path(saved['consensus_path']).parent
    key=digest({'policy':POLICY,'pin':PIN,'system':DUAL_SYSTEM,'request':part,'selection':independent.SELECTION,
            'helper_sha256':file_digest(Path(independent.__file__))})
    if (saved.get('policy')!=POLICY or saved.get('request_sha256')!=key or folder.name!=key
            or file_digest(folder/'consensus.json')!=saved['consensus_sha256']):
        raise ValueError('YO independent consensus changed')
    manifest=read(folder/'consensus.json')
    selections=independent.verify(manifest,folder,dual_items(part),part,policy=POLICY,system=DUAL_SYSTEM,pin=PIN,validate_provider=validate_provider)
    if saved['answer']!=decoded_answer(part,selections) or saved['selections']!=selections or saved['metadata']!=manifest['passes'][-1]['metadata']:
        raise ValueError('YO selected output differs from conditional independent opinions')
    return saved


def adjudicate(out,req):
    batches=[]
    for start in range(0,len(req['items']),BATCH):
        part={'task':req['task'],'items':req['items'][start:start+BATCH]}
        key=digest({'policy':POLICY,'pin':PIN,'system':DUAL_SYSTEM,'request':part,'selection':independent.SELECTION,
            'helper_sha256':file_digest(Path(independent.__file__))})
        folder=out/'batches'/key;done=folder/'batch.json'
        if done.exists():saved=validate_batch(read(done))
        else:
            try:manifest=independent.run(dual_items(part),part,folder,policy=POLICY,system=DUAL_SYSTEM,pin=PIN,
                call=lambda system,request,schema,**kwargs:call(system,request,schema,out,key[:8],**kwargs),validate_provider=validate_provider)
            except ValueError:
                immutable(out/'unresolved.json',{'policy':POLICY,'status':'invalid_or_missing_independent_vote','batch':str(folder),
                    'request':part,'human_approval':False});raise
            saved={'policy':POLICY,'request':part,'request_sha256':key,'answer':decoded_answer(part,manifest['selected']),
                'metadata':manifest['passes'][-1]['metadata'],'selections':manifest['selected'],
                'consensus_path':str(folder/'consensus.json'),'consensus_sha256':file_digest(folder/'consensus.json')}
            immutable(done,saved);validate_batch(saved)
        batches.append(saved)
    return batches


def validate_frozen_answer(path):
    saved=read(path);request=read(path.parent/'yo.request.json')
    if saved.get('policy')!=POLICY or saved['request_sha256']!=file_digest(path.parent/'yo.request.json'):
        raise ValueError('YO frozen answer is not the conditional independent-check policy')
    batches=[validate_batch(batch) for batch in saved['independent_batches']]
    merged={'decisions':[d for batch in batches for d in batch['answer']['decisions']]}
    validate_answer(merged,request['request'])
    if merged!=saved['answer']:raise ValueError('YO frozen decisions changed')
    expected=yo.decisions_from(request['bindings'],merged);selections={s['id']:s for batch in batches for s in batch['selections']}
    expected=[{**d,'two_luna':selections[f'y{n:04d}']} for n,d in enumerate(expected)]
    if saved['decisions']!=expected:raise ValueError('YO frozen occurrence/two-pass records changed')
    return saved


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chapters', nargs='+', default=['c0001', 'c0002', 'c0003'])
    p.add_argument('--generations', nargs='+', default=['audio-palette-v3-full12', 'audio-palette-v3-descriptors'])
    p.add_argument('--output', type=Path, default=FOLDER/'yo-restoration-luna-change-check-v3')
    p.add_argument('--book-fixes', type=Path, help='Apply the same verified spoken-text fix list as the target render')
    p.add_argument('--dry-run', action='store_true', help='Count classes and print the adjudication rows without calling the provider')
    a = p.parse_args(); out = a.output
    book = read(FOLDER/'source/book.json'); names, common = lexicon(book, read(FOLDER/'name-stress-v1/characters.answer.json')['answer'])
    texts = native_texts(a.generations, a.chapters, with_specs=True, book_fixes=a.book_fixes)
    decisions, rows, stats = analyse_texts(texts, names, common)
    print('units', len(texts), 'stats', json.dumps(stats), 'auto decisions', len(decisions), 'adjudication rows', len(rows), flush=True)
    if a.dry_run:
        for r in rows:print(r['chapter'], r['kind'], r['surface'], '->', r['yo'], json.dumps(r['witnesses'], ensure_ascii=False), '|', r['sentence'][:90])
        return
    out.mkdir(parents=True, exist_ok=True)
    req = yo.request(rows)
    for item,row in zip(req['items'],rows,strict=True):
        item['upstream_witnesses']=row['witnesses']
        if row.get('dictionary_forms'):item['dictionary_forms']=row['dictionary_forms']
    bound = {'rule': yo.RULE,'policy':POLICY,'provider':PIN[0],'model':PIN[1],'effort':PIN[2], 'system': yo.SYSTEM, 'request': req, 'bindings': [{k: r[k] for k in ('sha256', 'word', 'index', 'surface', 'yo', 'kind', 'chapter', 'witnesses')} for r in rows],
             'auto_decisions_sha256': digest(decisions), 'chapters': a.chapters, 'generations': a.generations,
             'book_fixes_sha256': file_digest(a.book_fixes) if a.book_fixes else None,
             'versions': yo._versions()}
    path = out/'yo.request.json'
    if path.exists() and read(path) != bound:raise SystemExit('Frozen yo request changed; use a new --output')
    if not path.exists():write_json(path, bound)
    batches = adjudicate(out, req) if rows else []
    merged = {'decisions': [d for b in batches for d in b['answer']['decisions']]}
    yo.validate(merged, req)
    selections={s['id']:s for batch in batches for s in batch['selections']}
    if decisions:raise ValueError('Automatic ambiguous YO choices are forbidden')
    frozen_decisions=[{**d,'two_luna':selections[f'y{n:04d}']} for n,d in enumerate(yo.decisions_from(rows,merged))]
    saved = {'rule': yo.RULE,'policy':POLICY,'selection_policy':independent.SELECTION, 'request_sha256': file_digest(path), 'answer': merged, 'stats': stats,
             'decisions': frozen_decisions,
             'metadata': {'batches': [{'request_sha256': b['request_sha256'], **b['metadata']} for b in batches]},
             'independent_batches':batches,
             'human_approval': False, 'scope': 'Text-only YO preparation; no audio, recognition or listening approval'}
    immutable(out/'yo.answer.json', saved)
    import collections
    print('choices', dict(collections.Counter(d['choice'] for d in merged['decisions'])), 'decisions', len(saved['decisions']))


if __name__ == '__main__':main()
