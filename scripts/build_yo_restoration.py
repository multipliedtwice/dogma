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

BATCH = 25
POLICY = 'yo-all-ambiguous-luna-low-v1'
PIN = ('codex_cli', 'gpt-6-luna', 'low')


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


def call(system,body,contract,out,tag):
    executable=Path(codex_analysis.codex_executable())
    version=run_cli([str(executable),'--version'],timeout=20)
    if version.returncode or not version.stdout.strip():raise ValueError('Cannot pin Codex CLI version')
    metadata=dict(zip(('provider','model','effort'),PIN))|{'policy':POLICY,'requested_model':PIN[1],
        'model_provenance':'explicit-cli-model-selection','cli_version':version.stdout.strip(),
        'executable':str(executable),'executable_sha256':file_digest(executable),
        'structured_call_sha256':file_digest(Path(codex_analysis.__file__))}
    answer=codex_analysis.structured_call(system,body,contract,model=PIN[1],reasoning_effort=PIN[2])
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


def adjudicate(out, req):
    items = req['items']; batches = []
    for start in range(0, len(items), BATCH):
        part = {'task': req['task'], 'items': items[start:start+BATCH]}
        ids = [i['id'] for i in part['items']]; contract = yo.schema(ids)
        bound = {'policy':POLICY,'provider':PIN[0],'model':PIN[1],'effort':PIN[2],
                 'system': yo.SYSTEM, 'request': part, 'schema': contract}
        sha = digest(bound); done = out/'batches'/f'batch-{sha[:16]}.answer.json'
        if done.exists():
            saved = read(done)
            if saved.get('request_sha256')!=sha or saved.get('ids')!=ids:raise ValueError('Cached YO request binding changed')
            ap=out/'batches'/f"batch-{sha[:16]}.attempt-{saved['attempts']-1}.answer.json"
            if file_digest(ap)!=saved.get('attempt_answer_sha256') or any(read(ap).get(k)!=saved[k] for k in ('answer','metadata')):
                raise ValueError('Cached YO answer differs from immutable attempt evidence')
            validate_provider(saved['metadata']);validate_answer(saved['answer'],part);batches.append(saved);continue
        body = part
        for attempt in range(3):
            path = out/'batches'/f'batch-{sha[:16]}.attempt-{attempt}.request.json'
            existed=path.exists()
            immutable(path, {**bound, 'request': body})
            print('YO BATCH', sha[:8], len(ids), 'items attempt', attempt, flush=True)
            ap=path.with_name(path.name.replace('.request', '.answer'));fp=path.with_name(path.name.replace('.request','.failure'))
            if ap.exists():
                result=read(ap)
                if result['request_sha256']!=file_digest(path):raise ValueError('YO attempt request changed')
                answer,metadata=result['answer'],result['metadata']
            elif fp.exists():
                if read(fp).get('request_sha256')!=file_digest(path):raise ValueError('YO failure request binding changed')
                continue
            else:
                if existed:raise ValueError('Unfinished immutable YO attempt; inspect prior worker before a new output')
                try:answer,metadata=call(yo.SYSTEM,body,contract,out,sha[:8])
                except Exception as error:
                    immutable(fp,{'request_sha256':file_digest(path),'error':type(error).__name__+': '+str(error)})
                    if attempt==2:raise
                    continue
                immutable(ap,{'answer':answer,'metadata':metadata,'request_sha256':file_digest(path)})
            validate_provider(metadata)
            try:validate_answer(answer, part)
            except (ValueError, KeyError, TypeError) as exc:
                if attempt == 2:raise
                body = {**part, 'validation_errors': str(exc)[:1500]}; continue
            saved = {'request_sha256': sha, 'ids': ids, 'answer': answer, 'metadata': metadata, 'attempts': attempt + 1,
                     'attempt_answer_sha256':file_digest(ap)}
            immutable(done, saved); batches.append(saved); break
        else:raise ValueError('YO bounded attempts exhausted; preserved failures require review')
    return batches


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chapters', nargs='+', default=['c0001', 'c0002', 'c0003'])
    p.add_argument('--generations', nargs='+', default=['audio-palette-v3-full12', 'audio-palette-v3-descriptors'])
    p.add_argument('--output', type=Path, default=FOLDER/'yo-restoration-all-luna-v1')
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
    saved = {'rule': yo.RULE,'policy':POLICY, 'request_sha256': file_digest(path), 'answer': merged, 'stats': stats,
             'decisions': decisions + yo.decisions_from(rows, merged),
             'metadata': {'batches': [{'request_sha256': b['request_sha256'], **b['metadata']} for b in batches]},
             'human_approval': False, 'scope': 'Text-only YO preparation; no audio, recognition or listening approval'}
    immutable(out/'yo.answer.json', saved)
    import collections
    print('choices', dict(collections.Counter(d['choice'] for d in merged['decisions'])), 'decisions', len(saved['decisions']))


if __name__ == '__main__':main()
