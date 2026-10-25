"""Apply durable human stress notes to a derived private development generation.

Existing audio, preparation and attempts stay immutable. Only affected units get
new native requests; manual feedback never enables recognition or quality loops.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, os, re, shutil, subprocess, sys, time
from pathlib import Path
from functools import lru_cache

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'runtime/rimsky/store.py').is_file())
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import debug_higgs_one_shot as d
import render_higgs_book_trial as trial
from rimsky import listener_stress, russian_pronunciation as phonetics
from rimsky.name_stress import name_spans
from rimsky.store import Store
from rimsky.util import digest,file_digest

POLICY='development-private-listener-stress-repair-v4'
RESTORED_STRESS='restored-silero-ruaccent-opus-v1'
SELF=Path(__file__).resolve()
read,write=d.read,d.write


def inventory(folder):
    # Queue receipts are live control state owned by the caller. Never rewrite
    # source media, inputs, plans, failure receipts or publication metadata.
    return {p.relative_to(folder).as_posix():file_digest(p) for p in sorted(folder.rglob('*'))
        if p.is_file() and p.relative_to(folder).parts[0] not in ('listener-stress-queue','__pycache__')}


def copy_exact(source,target,sha=None):
    sha=sha or file_digest(source);d.basic.checked(source,sha)
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():d.basic.checked(target,sha)
    else:
        temporary=target.with_name(target.name+'.copy.tmp');shutil.copyfile(source,temporary)
        d.basic.checked(temporary,sha);os.replace(temporary,target)


def archived_preparation(ready,folder=None):
    """Recover exact historical source bytes; never execute rewritten witnesses."""
    archived={}
    for path,key in [('scripts/debug_higgs_one_shot.py','implementation_sha256'),
        ('scripts/render_public_demo_one_shot.py','generic_primitives_sha256'),
        ('scripts/render_higgs_book_trial.py','trial_preparation_sha256'),
        ('scripts/build_contextual_stress.py','context_builder_sha256')]:
        expected=ready[key];live=ROOT/path
        local=[folder/location/path for location in ('effective-implementation','original-implementation')] if folder else []
        stored=next((p for p in local if p.is_file() and file_digest(p)==expected),None)
        if stored:raw=stored.read_bytes()
        elif file_digest(live)==expected:raw=live.read_bytes()
        else:
            commits=subprocess.check_output(['git','log','--format=%H','--',path],cwd=ROOT,text=True).splitlines()
            raw=None
            for commit in commits:
                candidate=subprocess.check_output(['git','show',commit+':'+path],cwd=ROOT)
                if hashlib.sha256(candidate).hexdigest()==expected:raw=candidate;break
            if raw is None:raise ValueError('Exact original preparation implementation is unavailable: '+path)
        archived[path]=raw
    return archived


def validate_effective(initial,effective):
    """The bounded technical fallback can change only cap and one seed step."""
    a=copy.deepcopy(initial);b=copy.deepcopy(effective)
    for row in (a,b):row.pop('key',None)
    x,y=a['synthesis'],b['synthesis']
    if y['max_tokens'] not in (x['max_tokens'],2048) or not 0<x['max_tokens']<=y['max_tokens']<=2048:
        raise ValueError('Unapproved technical decoder budget')
    if y['sampling']['seed'] not in (x['sampling']['seed'],(x['sampling']['seed']+1)%(2**31)):
        raise ValueError('Unapproved technical seed change')
    if (y.get('max_tokens'),y['sampling']['seed'])!=(x.get('max_tokens'),x['sampling']['seed']):
        proof=y.get('listener_technical_recovery',{})
        if proof.get('policy') not in ('development-private-listener-stress-repair-v1','development-private-listener-stress-repair-v2','development-private-listener-stress-repair-v3',POLICY) or not proof.get('previous_session'):
            raise ValueError('Missing technical recovery evidence')
    for row in (x,y):
        row.pop('max_tokens');row.pop('token_cap',None);row.pop('listener_technical_recovery',None);row['sampling'].pop('seed')
    if a!=b or effective.get('key')!=digest(effective['synthesis']):raise ValueError('Technical recovery changed source or sound controls')


@lru_cache(maxsize=64)
def _frozen_document(path,checksum):
    return read(Path(path))


def frozen_document(path,checksum):
    # Recheck bytes on every access; cache only the expensive immutable JSON decode.
    d.basic.checked(path,checksum)
    return _frozen_document(str(path),checksum)


@lru_cache(maxsize=8)
def evidence_model_paths(manifest_json):
    # Historical evidence is checked once per exact pin; new synthesis still
    # performs model_guard freshly before EVERY physical attempt.
    pin,runner=d.basic.model_guard(json.loads(manifest_json))
    return str(pin.path),str(runner)


_verified_native_edges={}


def verify_native_derivation(folder,ready,*,bundled=False):
    derivation=ready.get('listener_stress_derivation')or {}
    if not derivation.get('base'):return
    repair_path=folder/'repair.json';repair=frozen_document(repair_path,file_digest(repair_path))
    current_sha=file_digest(folder/'ready.json')
    if (repair.get('derived_ready_sha256')!=current_sha or
        {k:v for k,v in repair.items()if k not in ('base_inventory','derived_ready_sha256')}!=derivation):
        raise ValueError('Native ancestry repair does not match frozen ready')
    if bundled:return # Newready carries an explicit hash for every self-contained session file.
    expected=repair.get('base_inventory');base=Path(derivation['base']).resolve()
    if not isinstance(expected,dict)or not expected or any(not isinstance(k,str)or not re.fullmatch(r'[0-9a-f]{64}',str(v))for k,v in expected.items()):
        raise ValueError('Native ancestry lacks an immutable base inventory')
    d.basic.checked(base/'ready.json',derivation['base_ready_sha256'])
    key=(str(folder),current_sha,file_digest(repair_path))
    if key not in _verified_native_edges:
        if inventory(base)!=expected:raise ValueError('Native ancestor bytes changed since materialization')
        _verified_native_edges[key]=(base,expected)


def recheck_native_derivations():
    for base,expected in _verified_native_edges.values():
        if inventory(base)!=expected:raise ValueError('Native ancestor changed during evidence validation')


def native_evidence(folder,ready,unit,row):
    """Resolve only a pinned parent chain; never search sibling generations."""
    session=row.get('session','')
    if not re.fullmatch(r'[0-9a-f]{64}',session):raise ValueError('Invalid native evidence session identity')
    current=folder.resolve();seen=set();source_sha=frozen_document(current/'manifest.json',ready['manifest_sha256'])['source_sha256'];native_pin=ready['native_manifest']
    for _ in range(32):
        if current in seen:raise ValueError('Cyclic native evidence ancestry')
        seen.add(current);d.basic.checked(current/'manifest.json',ready['manifest_sha256'])
        if ready['native_manifest']!=native_pin:raise ValueError('Native model/runner pin changed across ancestry')
        if frozen_document(current/'manifest.json',ready['manifest_sha256'])['source_sha256']!=source_sha:raise ValueError('Native evidence ancestry changed source')
        d.basic.checked(current/'units'/(unit['id']+'.json'),file_digest(folder/'units'/(unit['id']+'.json')))
        native=current/'native'/session
        binding=ready.get('listener_stress_derivation',{}).get('native_evidence_bindings',{}).get(unit['id'])
        if binding and not native.is_dir():raise ValueError('Declared copied native evidence is missing locally')
        verify_native_derivation(current,ready,bundled=bool(binding))
        if native.exists():
            if binding:
                if (binding.get('policy')!='immutable-native-evidence-v1' or binding['session']!=session or session not in binding['sessions']
                    or binding['unit_receipt_sha256']!=file_digest(current/'units'/(unit['id']+'.json'))):
                    raise ValueError('Copied native evidence binding changed')
                origin_ready=current/binding['ready_archive'];origin_manifest=current/binding['manifest_archive']
                d.basic.checked(origin_ready,binding['ready_sha256']);d.basic.checked(origin_manifest,binding['manifest_sha256'])
                original_ready=frozen_document(origin_ready,binding['ready_sha256']);original_folder=Path(binding['origin'])
                for sid,files in binding['sessions'].items():
                    if not re.fullmatch(r'[0-9a-f]{64}',sid):raise ValueError('Invalid archived native session')
                    for name,checksum in files.items():
                        if Path(name).is_absolute()or '..'in Path(name).parts:raise ValueError('Native evidence escaped session')
                        d.basic.checked(current/'native'/sid/name,checksum)
            else:
                original_ready=ready;original_folder=current;origin_ready=current/'ready.json';origin_manifest=current/'manifest.json'
            if frozen_document(origin_manifest,original_ready['manifest_sha256'])['source_sha256']!=source_sha:raise ValueError('Archived native source identity changed')
            d.basic.checked(origin_manifest,original_ready['manifest_sha256'])
            request=trial.sequence_request(unit['id'],unit['synthesis']);start=read(native/'physical-start.json')
            if read(native/'unit-result.json')!=row or start.get('unit')!=unit:
                raise ValueError('Selected listener unit differs from immutable physical evidence')
            if read(native/'requests.json')!={'requests':[request]} or start.get('request_sha256')!=digest(request) or row.get('request_sha256')!=digest(request):
                raise ValueError('Selected listener request changed')
            expected=digest({'request':request,'unit':unit,'ready_sha256':file_digest(origin_ready),
                'attempt':start['attempt'],'helper_sha256':start['helper_sha256']})
            if start.get('id')!=session or expected!=session:raise ValueError('Native reservation identity changed')
            if original_ready.get('native_manifest')!=ready.get('native_manifest'):raise ValueError('Native model/runner pin changed across ancestry')
            if original_ready.get('listener_stress_derivation',{}).get('helper_sha256')!=start['helper_sha256']:
                raise ValueError('Native helper implementation provenance changed')
            sessions={};pending=[session]
            while pending:
                sid=pending.pop()
                if sid in sessions:raise ValueError('Cyclic technical attempt evidence')
                if not re.fullmatch(r'[0-9a-f]{64}',sid):raise ValueError('Invalid previous native evidence identity')
                if binding and sid not in binding['sessions']:raise ValueError('Unbound copied technical attempt')
                attempt=current/'native'/sid;identity=read(attempt/'worker.json')
                if not stopped(identity):raise ValueError('Prior native worker still lives')
                terminal=read(attempt/'physical-result.json');attempt_result=read(attempt/'unit-result.json')
                if sid==session and terminal['returncode']!=0:
                    raise ValueError('Selected listener attempt did not finish')
                if sid!=session and attempt_result.get('status')!='technical_failure':raise ValueError('Prior technical attempt was not a failure')
                command=identity['command'];expected_origin=original_folder/'native'/sid
                model,runner=evidence_model_paths(json.dumps(original_ready['native_manifest'],sort_keys=True))
                expected_command=[runner,'--task','tts','--family','higgs_audio_tts','--model',model,'--backend','cuda','--device','0',
                    '--threads','4','--request-sequence',str(expected_origin/'requests.json'),'--out-dir',str(expected_origin),'--out-format','pcm16','--metrics']
                if command!=expected_command:
                    raise ValueError('Native worker request/output provenance changed')
                physical=read(attempt/'physical-start.json');attempt_unit=physical['unit']
                if attempt_unit['id']!=unit['id'] or physical['id']!=sid:raise ValueError('Prior attempt changed source unit')
                attempt_request=trial.sequence_request(unit['id'],attempt_unit['synthesis'])
                if read(attempt/'requests.json')!={'requests':[attempt_request]} or physical['request_sha256']!=digest(attempt_request):
                    raise ValueError('Prior technical request changed')
                if digest({'request':attempt_request,'unit':attempt_unit,'ready_sha256':file_digest(origin_ready),
                    'attempt':physical['attempt'],'helper_sha256':physical['helper_sha256']})!=sid:
                    raise ValueError('Prior physical reservation changed')
                if sid!=session:validate_effective(attempt_unit,unit)
                files={p.relative_to(attempt).as_posix():file_digest(p)for p in sorted(attempt.rglob('*'))if p.is_file()}
                sessions[sid]=files
                previous=attempt_unit['synthesis'].get('listener_technical_recovery',{}).get('previous_session')
                if previous:pending.append(previous)
            d.basic.checked(native/(unit['id']+'.wav'),row['audio_sha256'])
            return {'policy':'immutable-native-evidence-v1','unit':unit['id'],'session':session,'origin':str(original_folder),
                'ready_sha256':file_digest(origin_ready),'manifest_sha256':file_digest(origin_manifest),
                'unit_receipt_sha256':file_digest(current/'units'/(unit['id']+'.json')),
                'ready_archive':'native-evidence/readies/'+file_digest(origin_ready)+'.json',
                'manifest_archive':'native-evidence/manifests/'+file_digest(origin_manifest)+'.json',
                'sessions':sessions,'copy_from':str(current),'ready_from':str(origin_ready),'manifest_from':str(origin_manifest)}
        derivation=ready.get('listener_stress_derivation') or {}
        if not derivation.get('base') or not derivation.get('base_ready_sha256'):
            raise ValueError('Selected native evidence is absent from pinned ancestry')
        repair=frozen_document(current/'repair.json',file_digest(current/'repair.json'))
        if repair.get('base')!=derivation['base'] or repair.get('base_ready_sha256')!=derivation['base_ready_sha256']:
            raise ValueError('Native ancestry receipt differs from frozen ready')
        current=Path(derivation['base']).resolve();ready=frozen_document(current/'ready.json',derivation['base_ready_sha256'])
    raise ValueError('Native evidence ancestry exceeds bounded depth')


def copy_native_evidence(output,witness):
    copy_exact(Path(witness['ready_from']),output/witness['ready_archive'],witness['ready_sha256'])
    copy_exact(Path(witness['manifest_from']),output/witness['manifest_archive'],witness['manifest_sha256'])
    for sid,files in witness['sessions'].items():
        if not re.fullmatch(r'[0-9a-f]{64}',sid):raise ValueError('Invalid copied native session')
        for name,checksum in files.items():
            if Path(name).is_absolute()or '..'in Path(name).parts:raise ValueError('Copied native evidence escaped session')
            copy_exact(Path(witness['copy_from'])/'native'/sid/name,output/'native'/sid/name,checksum)


class VerifiedUnits(list):
    """List-compatible units carrying already-verified immutable witness records."""
    def __init__(self):
        super().__init__();self.native_evidence={}


def load_units(folder,ready,book):
    """Read hash-bound historical plans without rerunning obsolete preparation."""
    _verified_native_edges.clear()
    d.basic.checked(folder/'manifest.json',ready['manifest_sha256'])
    for name,key in [('prepared-inputs.json','preparation_sha256'),
        ('preparation/homograph/homograph.answer.json','homograph_answer_sha256'),
        ('preparation/yo/yo.answer.json','yo_answer_sha256'),('preparation/context/answer.json','context_answer_sha256')]:
        if ready.get(key):d.basic.checked(folder/name,ready[key])
    units=VerifiedUnits();blocks={b['id']:b.get('text','') for c in book['chapters'] for b in c['blocks']}
    for rec in ready['plans']:
        d.basic.checked(folder/rec['assignment'],rec['sha256']);path=d.basic.checked(folder/rec['plan'],rec['plan_sha256'])
        for unit in read(path)['units']:
            if blocks.get(unit['source_id'],'')[unit['start']:unit['end']]!=unit['text']:
                raise ValueError('Frozen unit differs from the exact source slice: '+unit['id'])
            row=read(folder/'units'/(unit['id']+'.json'))
            if row.get('effective_unit'):
                validate_effective(unit,row['effective_unit']);unit=row['effective_unit']
                units.native_evidence[unit['id']]=native_evidence(folder,ready,unit,row)
            if row.get('unit')!=unit['id'] or row.get('status')!='rendered':
                raise ValueError('Listener repair requires a complete source unit: '+unit['id'])
            if unit['synthesis']['backend']=='nonverbal_asset':
                if row.get('source_asset')!=unit['synthesis']:raise ValueError('Nonverbal source changed')
            elif row.get('synthesis_sha256')!=digest(unit['synthesis']):raise ValueError('Frozen synthesis binding changed')
            audio=(folder/row['audio']).resolve()
            if not audio.is_relative_to((folder/'assets').resolve()):raise ValueError('Source audio escaped immutable assets')
            d.basic.checked(audio,row['audio_sha256']);d.basic.basic_audio(audio)
            units.append(unit)
    if len(units)!=ready['unit_count'] or len({u['id'] for u in units})!=len(units):raise ValueError('Source coverage changed')
    for cid,count in ready['chapter_counts'].items():
        if sum(u['chapter_id']==cid for u in units)!=count:raise ValueError('Source chapter coverage changed')
    recheck_native_derivations()
    return units,blocks


def restoration_inputs(base,ready):
    """Full archived text witnesses, bound by the original answer checksum."""
    if not ready.get('context_answer_sha256'):return None
    answer_path=d.basic.checked(base/'preparation/context/answer.json',ready['context_answer_sha256'])
    answer=read(answer_path);path=d.basic.checked(answer_path.parent/'inputs.json',answer['inputs_sha256'])
    inputs=read(path)
    from rimsky import contextual_stress
    if inputs['module_sha256']!=file_digest(Path(contextual_stress.__file__)):
        raise ValueError('Exact contextual replay implementation is unavailable')
    return {'answer':answer,'inputs':inputs['inputs'],'answer_sha256':file_digest(answer_path),
        'inputs_sha256':file_digest(path),'module_sha256':inputs['module_sha256']}


def restored_baseline(unit,witnesses):
    """Replay the entire archived layer before restoring its exact input text."""
    if unit['synthesis']['backend']!='higgs_audio_cpp':return None,None
    old=unit['synthesis'];context=old.get('contextual_stress')
    previous=old.get('russian_phonetics');before=previous['prephonetic_source'] if previous else old['text']
    if not context:return before,None
    if not witnesses or context['answer_sha256']!=witnesses['answer_sha256'] or context['module_sha256']!=witnesses['module_sha256']:
        raise ValueError('Context rollback witness does not bind the frozen synthesis')
    expected=(old.get('listener_stress') or {}).get('pre_listener_source',before)
    from rimsky import contextual_stress as rules
    block={k:witnesses['answer'][k] for k in ('rule','dictionary_sha256','decisions')}
    block['answer_sha256']=witnesses['answer_sha256'];matched=[]
    protected={(row['word'],row['index']) for row in context['protected_occurrences']}
    for index,item in enumerate(witnesses['inputs']):
        # The original builder deduplicated identical full native strings across
        # chapters. Its chapter field describes the first witness, not its scope.
        if rules.plain(item['selected'])!=rules.plain(expected):continue
        automatic=item.get('upstream_evidence',{}).get('automatic_pronunciation')
        if automatic!=old.get('automatic_pronunciation'):continue
        replay,proof=rules.apply(item['source'],item['selected'],block,item.get('names'),item.get('common',()),protected)
        if replay==expected and {**proof,'module_sha256':witnesses['module_sha256']}==context:
            matched.append((index,item))
    if len(matched)!=1:raise ValueError('Context rollback requires one exact full-text replay witness: '+unit['id'])
    index,item=matched[0]
    return item['selected'],{'policy':RESTORED_STRESS,'answer_sha256':witnesses['answer_sha256'],
        'inputs_sha256':witnesses['inputs_sha256'],'input_index':index,'input_sha256':digest(item),
        'restored_text_sha256':digest(item['selected']),'archived_output_sha256':digest(expected),
        'replay_module_sha256':witnesses['module_sha256'],'source_unit':unit['id'],
        'historical_contextual_stress':context}


def corrected_unit(unit,frozen,blocks,forced=False,report=None,baseline=None,restoration=None):
    if unit['synthesis']['backend']!='higgs_audio_cpp':return copy.deepcopy(unit),None
    old=unit['synthesis'];previous=old.get('russian_phonetics')
    before=previous['prephonetic_source'] if previous else old['text']
    if previous:phonetics.validate(before,old['text'],previous['records'])
    if baseline is not None:before=baseline
    after,evidence=listener_stress.apply(before,frozen,source_id=unit['source_id'],source_text=blocks[unit['source_id']],
        source_start=unit['start'],source_end=unit['end'])
    native,records=phonetics.apply(after);phonetics.validate(after,native,records)
    if native==old['text'] and not forced:return copy.deepcopy(unit),None
    if not old['prompt'].endswith(old['text']):raise ValueError('Cannot bind native prompt suffix')
    result=copy.deepcopy(unit);plan=result['synthesis']
    if restoration:
        plan.pop('contextual_stress',None);plan['stress_baseline_restoration']=restoration
    proof={**evidence,'module_sha256':file_digest(Path(listener_stress.__file__)),
        'pre_listener_source':before,'source_id':unit['source_id'],'source_range':[unit['start'],unit['end']],
        'source_passage_sha256':digest(blocks[unit['source_id']])}
    if forced:
        reports=report if isinstance(report,list) else [report]
        families=[r for r in reports if r.get('force_scope')=='proper_name_family']
        heard=[r['origin_report'] for r in families if r['heard_target']]
        heard.extend(r for r in reports if r.get('force_scope')!='proper_name_family')
        if heard:
            proof['reported_audio']={'reports':[{'report_id':r['id'],'audio_sha256':r.get('audio_hash')} for r in heard],
                'report_id':heard[0]['id'],'audio_sha256':heard[0].get('audio_hash'),
                'reason':'Explicit listener regeneration of this source occurrence'}
        if families:
            proof['family_regeneration']={'policy':'latest-human-family-all-mentions-v1',
                'snapshot_sha256':frozen['sha256'],'targets':families,
                'reason':'Explicit human family correction regenerates every matching source mention; unheard targets are not listening evidence'}
    proof['sha256']=digest({k:v for k,v in proof.items() if k!='sha256'})
    plan.update(text=native,prompt=old['prompt'][:-len(old['text'])]+native,listener_stress=proof,
        russian_phonetics={'rule':phonetics.RULE,'prephonetic_source':after,'records':records,
            'module_sha256':file_digest(Path(phonetics.__file__))})
    if native==old['text']:
        plan['sampling']['seed']=(old['sampling']['seed']+1)%(2**31)
        proof['same_text_seed']={'original':old['sampling']['seed'],'effective':plan['sampling']['seed']}
        proof['sha256']=digest({k:v for k,v in proof.items() if k!='sha256'})
    plan['take']=old.get('take',0)+1
    result['retake_number']=unit.get('retake_number',0)+1;result['key']=digest(plan)
    a=trial.sequence_request(unit['id'],old);b=trial.sequence_request(unit['id'],plan)
    for value in (a,b):value.pop('text');value.pop('seed')
    if a!=b:raise ValueError('Human correction changed unrelated native controls')
    return result,proof


def family_spans(text,lexicon):
    """Exact inflections only, with mixed-script/digit token boundaries guarded."""
    plain,starts,ends=listener_stress._plain_map(text)
    return [(starts[a],ends[b-1],marked) for a,b,marked in name_spans(plain,lexicon,implicit_single=True)
        if (a==0 or not(plain[a-1].isalnum() or plain[a-1]=='_'))
        and (b==len(plain) or not(plain[b].isalnum() or plain[b]=='_'))]


def family_already_regenerated(unit,origin,family,blocks):
    """A verified loaded take consumes this exact origin for this exact target."""
    proof=unit['synthesis'].get('listener_stress') or {}
    prior=proof.get('family_regeneration')
    if not prior:return False
    if prior.get('policy')!='latest-human-family-all-mentions-v1':
        raise ValueError('Unknown existing family regeneration receipt policy')
    if proof.get('sha256')!=digest({k:v for k,v in proof.items() if k!='sha256'}):
        raise ValueError('Existing family regeneration receipt checksum changed')
    if (proof.get('source_id')!=unit['source_id'] or proof.get('source_range')!=[unit['start'],unit['end']]
        or proof.get('source_passage_sha256')!=digest(blocks[unit['source_id']])):
        raise ValueError('Existing family regeneration receipt lost its exact target source')
    return any(row.get('force_scope')=='proper_name_family'
        and row.get('origin_report')==origin and row.get('origin_report_sha256')==digest(origin)
        and row.get('families')==family and row.get('target_unit')==unit['id']
        and row.get('target_source_id')==unit['source_id']
        and row.get('target_source_range')==[unit['start'],unit['end']]
        and row.get('target_passage_sha256')==digest(blocks[unit['source_id']])
        for row in prior.get('targets',[]))


def note_coverage(notes,units,blocks,changes,frozen):
    """Every raw note gets an explicit current-source disposition, never silence."""
    active={row['report_id'] for row in frozen.get('occurrences',[])};result=[]
    for entry in notes:
        report=entry['report'];identifier=report['id'];targets=[];reason=''
        family=entry.get('family');occurrence=entry.get('occurrence')
        if family:
            active_family={k:v for k,v in family.items() if frozen.get('names',{}).get(k)==v}
            targets=[u['id'] for u in units if family_spans(u['text'],active_family)] if active_family else []
            wholebook=sum(len(family_spans(text,active_family)) for text in blocks.values()) if active_family else 0
            reason='Family note applies book-wide' if targets else 'Superseded family note or no occurrence in selected chapters'
        elif occurrence:
            wholebook=0
            if identifier not in active:reason='Superseded by a later occurrence or family note'
            elif occurrence['source_id'] not in blocks:reason='Source passage is not in this edition'
            elif digest(blocks[occurrence['source_id']])!=occurrence['passage_sha256']:reason='Exact source passage hash differs'
            else:
                start=occurrence['text_offset'];end=start+len(occurrence['selected_text'])
                targets=[u['id'] for u in units if u['source_id']==occurrence['source_id'] and u['start']<=start and end<=u['end']]
                wholebook=1;reason='Exact source occurrence' if targets else 'Occurrence is outside selected rendered chapters'
        else:wholebook=0;reason='Raw note has no formal source-bound stress choice; preserved for review'
        status='applied' if any(uid in changes for uid in targets) else 'nochange' if targets else 'unbound'
        result.append({'report_id':identifier,'kind':report.get('kind'),'status':status,'reason':reason,
            'current_units':targets,'wholebook_occurrences':wholebook,'selected_text':report.get('selected_text'),
            'intended_stress':report.get('intended_stress')})
    return result


def frozen_notes(frozen,notes):
    """Keep a later journal read inside the exact immutable snapshot boundary."""
    identifiers={row['report_id'] for row in frozen['note_statuses']}
    captured=[row for row in notes if row['report']['id'] in identifiers]
    if len(captured)!=len(identifiers) or {row['report']['id'] for row in captured}!=identifiers:
        raise ValueError('Frozen human report evidence is missing or duplicated')
    return captured,identifiers


def pending_reports(base,item,notes,generation,units,blocks,frozen_ids=None,frozen=None):
    """Coalesce valid queued origins; only audio actually heard can force a take."""
    canonical={row['report']['id']:row['report'] for row in notes}
    bindings={row['report']['id']:row for row in notes}
    candidates=[(None,item)]
    directories={base/'listener-stress-queue'}
    if item.get('_queue_item_path'):
        original_queue=Path(item['_queue_item_path']).resolve().parent
        if original_queue.name=='listener-stress-queue':directories.add(original_queue)
    control_ids={generation['id'],item.get('generation_id')}
    for path in sorted({p for directory in directories for p in directory.glob('*.json')}):
        row=read(path)
        if (row.get('report') and row.get('status') in ('queued','running','held')
            and row.get('generation_id') in control_ids):candidates.append((path,row))
    origins={};forced={};queued=[]
    fields=('id','source_id','text_offset','selected_text','intended_stress','audio_hash','generation_id','kind','created')
    for path,row in candidates:
        origin=row.get('origin_report') or row.get('original_report') or row['report']
        if frozen_ids is not None and origin['id'] not in frozen_ids:
            if path is None:
                raise ValueError('Primary human report arrived after the frozen snapshot; capture a fresh repair')
            # Feedback arriving during configuration belongs to the next job.
            # Do not consume it or force a take with an older pronunciation plan.
            continue
        if row.get('origin_report_sha256') and row['origin_report_sha256']!=digest(origin):
            raise ValueError('Queued human report origin checksum changed')
        expected=canonical.get(origin['id'])
        if expected is None or any(origin.get(k)!=expected.get(k) for k in fields):
            raise ValueError('Queued human report is not the canonical saved report: '+origin['id'])
        source=blocks.get(origin['source_id']);offset=origin.get('text_offset');word=origin.get('selected_text')
        if (source is None or type(offset)is not int or not isinstance(word,str)
            or source[offset:offset+len(word)]!=word or origin.get('passage_text')!=source):
            raise ValueError('Queued human report lost its exact source passage/offset: '+origin['id'])
        if origin.get('kind')!='stress' or not origin.get('intended_stress'):
            raise ValueError('Queued report has no explicit human stress intent: '+origin['id'])
        origins[origin['id']]=origin
        family=bindings[origin['id']].get('family') or {}
        active_family={key:marked for key,marked in family.items() if frozen
            and frozen.get('names',{}).get(key)==marked
            and frozen.get('family_evidence',{}).get(key,{}).get('report_id')==origin['id']}
        targets=[u for u in units if u['source_id']==origin['source_id']
            and u['start']<=offset and offset+len(word)<=u['end']]
        if len(targets)!=1:raise ValueError('Queued report crosses or misses current native coverage')
        unit=targets[0];heard=[s for s in generation['segments'] if s['id']==unit['id']]
        exact_heard=(len(heard)==1 and heard[0]['audio_hash']==origin.get('audio_hash')
            and heard[0]['source_id']==origin['source_id'])
        if active_family:
            for target in units:
                spans=family_spans(target['text'],active_family)
                if not spans:continue
                if family_already_regenerated(target,origin,active_family,blocks):continue
                if target['synthesis']['backend']!='higgs_audio_cpp':raise ValueError('Human family target is not synthesizable speech')
                forced.setdefault(target['id'],{})[origin['id']]={'force_scope':'proper_name_family',
                    'origin_report':origin,'origin_report_sha256':digest(origin),'families':active_family,
                    'heard_target':bool(exact_heard and target['id']==unit['id']),
                    'target_unit':target['id'],'target_source_id':target['source_id'],
                    'target_source_range':[target['start'],target['end']],
                    'target_passage_sha256':digest(blocks[target['source_id']]),
                    'target_synthesis_sha256':digest(target['synthesis']),
                    'occurrences':[{'start':target['start']+a,'end':target['start']+b,
                        'source_text':target['text'][a:b],'intended_stress':marked}for a,b,marked in spans]}
        elif not family and (len(heard)==1 and heard[0]['audio_hash']==origin.get('audio_hash')
            and heard[0]['source_id']==origin['source_id']):
            if unit['synthesis']['backend']!='higgs_audio_cpp':raise ValueError('Human stress target is not a synthesizable speech unit')
            forced.setdefault(unit['id'],{})[origin['id']]=origin
        if path:
            queued.append({'path':str(path.resolve()),'id':row['id'],'origin_report_id':origin['id'],
                'origin_report_sha256':digest(origin)})
    return [origins[k] for k in sorted(origins)],{uid:[items[k] for k in sorted(items)] for uid,items in forced.items()},queued


def gateway_configuration(data_dir):
    path=ROOT/'training-work/local-services/config.json'
    if not path.exists():return None
    value=json.loads(path.read_text('utf-8-sig'));entry=value.get('listener_correction_gateway')
    if not entry:return None
    helper=(ROOT/entry['helper']).resolve();d.basic.checked(helper,entry['sha256'])
    runtime=next((s for s in value['services'] if s['name']=='runtime'),None)
    if not runtime or runtime['args'].count('--data-dir')!=1:return None
    actual=(ROOT/runtime['args'][runtime['args'].index('--data-dir')+1]).resolve()
    if actual!=data_dir.resolve():return None
    return {'config':str(path.resolve()),'helper':str(helper),'sha256':entry['sha256'],'data_dir':str(data_dir.resolve())}


def handoff_gateway(output,config,ready,data_dir,final=False):
    """Only adopt the managed view that is still on this exact source job."""
    binding=config.get('gateway_binding')
    if not binding:return {'status':'not_configured'}
    d.basic.checked(Path(binding['helper']),binding['sha256'])
    if Path(binding['data_dir']).resolve()!=data_dir.resolve():raise ValueError('Gateway library binding changed')
    import importlib.util
    previous=sys.dont_write_bytecode;sys.dont_write_bytecode=True
    try:
        spec=importlib.util.spec_from_file_location('listener_correction_gateway',binding['helper'])
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    finally:sys.dont_write_bytecode=previous
    module.CONFIG=Path(binding['config']);module.BOOK_ID=config['book_id'];module.DATA=data_dir.resolve()
    module.DATABASE=data_dir.resolve()/'library.sqlite3';module.TARGET_NAME=output.name
    module.OLD_GENERATION_ID=config['source_generation_id'];module.LOCK=output/'gateway-repin.lock'
    module.CHAPTERS=tuple(ready['chapters']);module.PREFIX_CHAPTER=ready['chapters'][0]
    current=module._service('gateway');args=current['args']
    if args.count('--generation-id')!=1 or args.count('--book-id')!=1:raise ValueError('Ambiguous managed gateway identity')
    actual=args[args.index('--generation-id')+1];book=args[args.index('--book-id')+1]
    target=module.generation_id(output.name)
    if book!=config['book_id'] or actual not in (config['source_generation_id'],target):
        return {'status':'not_targeted','reason':'Managed view moved away from the repaired source'}
    try:module.readiness(output.name,verify_audio=True)
    except (OSError,ValueError,KeyError) as error:
        if final:raise
        return {'status':'waiting_for_verified_prefix','reason':str(error)}
    if actual==target:
        return {'status':'applied','published_generation_id':target,'already_repinned':True}
    proof=module.apply(output.name)
    write(output/'gateway-repin.json',proof,immutable=True)
    return {'status':'applied','published_generation_id':target,'proof':proof}


def configure(base,data_dir,generation_id,item):
    ready=read(base/'ready.json');manifest=read(base/'manifest.json')
    source=base.parent/'source/book.json';d.basic.checked(source,manifest['source_sha256'])
    book_source=read(source)
    store=Store(data_dir,correction_profile=listener_stress.shared_profile())
    try:
        generation=store.generation(generation_id);book=store.book(generation['book_id'])
        origin=(generation.get('analysis') or {}).get('external_preview',{}).get('generation')
        if origin!=base.name or book['id']!=book_source['id']:raise ValueError('Store/generation/source identity mismatch')
        frozen=listener_stress.snapshot(store,book)
        notes,frozen_ids=frozen_notes(frozen,listener_stress.notes(store,book))
        report=item['report'];heard=[s for s in generation['segments'] if s['id']==report['segment_id']]
        if (len(heard)!=1 or heard[0]['source_id']!=report['source_id'] or heard[0]['audio_hash']!=report.get('audio_hash')):
            raise ValueError('Reported production reader take changed before repair')
        d.basic.checked(data_dir/'audio'/(heard[0]['audio_hash']+'.wav'),heard[0]['audio_hash'])
    finally:store.close()
    units,blocks=load_units(base,ready,book_source)
    origins,forced,queued=pending_reports(base,item,notes,generation,units,blocks,frozen_ids,frozen)
    witnesses=restoration_inputs(base,ready)
    effective=[];changes={};restorations={}
    for unit in units:
        baseline,restoration=restored_baseline(unit,witnesses)
        if restoration:restorations[unit['id']]=restoration
        new,evidence=corrected_unit(unit,frozen,blocks,unit['id'] in forced,forced.get(unit['id']),baseline,restoration)
        effective.append(new)
        if evidence:changes[unit['id']]=evidence
    key=digest({'base_ready_sha256':file_digest(base/'ready.json'),'snapshot':frozen,'changes':changes,
        'stress_policy':RESTORED_STRESS,'restorations':restorations,'helper_sha256':file_digest(SELF)})
    inherited={uid:proof for uid,proof in getattr(units,'native_evidence',{}).items()if uid not in changes}
    output=base.parent/('listener-stress-'+book['id'][:8]+'-'+key[:12])
    config={'policy':POLICY,'base':str(base),'base_ready_sha256':file_digest(base/'ready.json'),
        'source_sha256':file_digest(source),'book_id':book['id'],'source_generation_id':generation_id,
        'helper_sha256':file_digest(SELF),'listener_module_sha256':file_digest(Path(listener_stress.__file__)),
        'snapshot_sha256':frozen['sha256'],'affected_ids':list(changes),'priority_unit':sorted(forced or changes or {units[0]['id']:None})[0],
        'unit_count':len(units),'pending_origins':origins,'forced_reports':forced,'coalesced_queue_items':queued,
        'automatic_stress_policy':RESTORED_STRESS,'restoration_witnesses':restorations,
        'native_evidence_bindings':inherited,
        'gateway_binding':gateway_configuration(data_dir),
        'reused':len(units)-len(changes),'historical_implementation_files':{p:hashlib.sha256(b).hexdigest() for p,b in archived_preparation(ready,base).items()},
        'post_processing':'off',**d.UNCHECKED}
    return output,config,ready,units,effective,blocks,frozen,notes


def copy_source_coverage(base,output,ready):
    if ready.get('source_coverage_sha256'):
        copy_exact(base/'source-coverage.json',output/'source-coverage.json',ready['source_coverage_sha256'])


def materialize(output,config,ready,units,effective,blocks,frozen,notes):
    base=Path(config['base']);before=inventory(base)
    if (output/'repair.json').exists():
        prior=read(output/'repair.json')
        if {k:v for k,v in prior.items() if k not in ('base_inventory','derived_ready_sha256')}!=config or before!=prior['base_inventory']:
            raise ValueError('Reserved listener repair inputs changed')
        d.basic.checked(output/'ready.json',prior['derived_ready_sha256'])
        return prior,read(output/'ready.json')
    for directory in ('preparation','scene-music-v2','ambiguity-report'):
        for path in sorted((base/directory).rglob('*')):
            if path.is_file():copy_exact(path,output/path.relative_to(base))
    for name,raw in archived_preparation(ready,base).items():
        target=output/'original-implementation'/name;target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():d.basic.checked(target,hashlib.sha256(raw).hexdigest())
        else:target.write_bytes(raw)
    for name in ('prepared-inputs.json','section-markers.json','source-copy-manifest.json'):
        if (base/name).exists():copy_exact(base/name,output/name)
    copy_source_coverage(base,output,ready)
    write(output/'listener-stress-snapshot.json',frozen,immutable=True)
    for queued in config['coalesced_queue_items']:
        path=Path(queued['path']);row=read(path);origin=row.get('origin_report') or row.get('original_report') or row['report']
        if row['id']!=queued['id'] or digest(origin)!=queued['origin_report_sha256']:
            raise ValueError('Coalesced queue origin changed before reservation')
        copy_exact(path,output/'queue-evidence'/(path.stem+'-'+file_digest(path)+'.json'))
    changed=set(config['affected_ids']);new_by_id={u['id']:u for u in effective};new_ready=copy.deepcopy(ready)
    for record in new_ready['plans']:
        assignment=d.listener_assignment(read(base/record['assignment']),blocks,frozen)
        assignment['pronunciation'].pop('contextual_stress',None)
        assignment['pronunciation']['contextual_stress_policy']=RESTORED_STRESS
        write(output/record['assignment'],assignment,immutable=True);record['sha256']=file_digest(output/record['assignment'])
        plan=read(base/record['plan']);plan['units']=[new_by_id[u['id']] for u in plan['units']]
        plan['listener_stress_derivation']={'policy':POLICY,'base_plan_sha256':record['plan_sha256'],'snapshot_sha256':frozen['sha256']}
        write(output/record['plan'],plan,immutable=True);record['plan_sha256']=file_digest(output/record['plan'])
    for unit in units:
        if unit['id'] in changed:continue
        row=read(base/'units'/(unit['id']+'.json'))
        copy_exact(base/row['audio'],output/row['audio'],row['audio_sha256'])
        copy_exact(base/'units'/(unit['id']+'.json'),output/'units'/(unit['id']+'.json'))
        if unit['id']in config.get('native_evidence_bindings',{}):
            witness=config['native_evidence_bindings'][unit['id']]
            copy_native_evidence(output,witness)
    manifest=read(base/'manifest.json');manifest.update(render_policy=POLICY,listener_stress_derivation=config)
    manifest['pronunciation'].pop('contextual_stress',None)
    manifest['pronunciation']['contextual_stress_policy']=RESTORED_STRESS
    write(output/'manifest.json',manifest,immutable=True)
    new_ready.update(policy=POLICY,manifest_sha256=file_digest(output/'manifest.json'),
        contextual_stress_policy=RESTORED_STRESS,
        listener_stress_snapshot_sha256=file_digest(output/'listener-stress-snapshot.json'),
        listener_stress_module_sha256=config['listener_module_sha256'],
        listener_stress_derivation=config,implementation_sha256=file_digest(d.SELF),
        trial_preparation_sha256=file_digest(Path(trial.__file__)))
    write(output/'ready.json',new_ready,immutable=True)
    own_files={}
    for name,key in [('scripts/debug_higgs_one_shot.py','implementation_sha256'),
        ('scripts/render_public_demo_one_shot.py','generic_primitives_sha256'),
        ('scripts/render_higgs_book_trial.py','trial_preparation_sha256'),
        ('scripts/build_contextual_stress.py','context_builder_sha256')]:
        copy_exact(ROOT/name,output/'effective-implementation'/name,new_ready[key]);own_files[name]=new_ready[key]
    for name,path,sha in [('scripts/repair_debug_listener_stress.py',SELF,config['helper_sha256']),
        ('runtime/rimsky/listener_stress.py',Path(listener_stress.__file__),config['listener_module_sha256'])]:
        copy_exact(path,output/'effective-implementation'/name,sha);own_files[name]=sha
    write(output/'effective-implementation/manifest.json',{'ready_sha256':file_digest(output/'ready.json'),
        'files':own_files,'policy':'exact-effective-preparation-source-archive-v2'},immutable=True)
    coverage=note_coverage(notes,units,blocks,set(config['affected_ids']),frozen)
    write(output/'listener-note-coverage.json',{'notes':coverage,'raw_count':len(notes),'snapshot_sha256':frozen['sha256']},immutable=True)
    config={**config,'base_inventory':before,'derived_ready_sha256':file_digest(output/'ready.json')}
    write(output/'repair.json',config,immutable=True)
    return config,new_ready


def stopped(identity):
    import psutil
    try:return abs(psutil.Process(identity['pid']).create_time()-identity['create_time'])>=.01
    except psutil.NoSuchProcess:return True


def native_attempt(output,ready,unit,ordinal):
    request=trial.sequence_request(unit['id'],unit['synthesis'])
    sid=digest({'request':request,'unit':unit,'ready_sha256':file_digest(output/'ready.json'),'attempt':ordinal,'helper_sha256':file_digest(SELF)})
    folder=output/'native'/sid;folder.mkdir(parents=True,exist_ok=True)
    session={'id':sid,'unit':unit,'request_sha256':digest(request),'attempt':ordinal,'helper_sha256':file_digest(SELF)}
    write(folder/'requests.json',{'requests':[request]},immutable=True)
    if (folder/'physical-start.json').exists():
        if read(folder/'physical-start.json')!=session:raise ValueError('Reserved correction attempt changed')
        if not (folder/'physical-result.json').exists():raise ValueError('Unfinished physical attempt; never duplicate it')
        if (folder/'worker.json').exists() and not stopped(read(folder/'worker.json')):raise ValueError('Prior native worker still lives')
    else:
        d.basic.assets.wait_for_audio_resources();pin,runner=d.basic.model_guard(ready['native_manifest'])
        d.validate_synthesis(unit['synthesis'],ready)
        with (folder/'physical-start.json').open('x',encoding='utf-8')as h:json.dump(session,h,ensure_ascii=False,indent=2)
        command=[str(runner),'--task','tts','--family','higgs_audio_tts','--model',str(pin.path),'--backend','cuda','--device','0',
            '--threads','4','--request-sequence',str(folder/'requests.json'),'--out-dir',str(folder),'--out-format','pcm16','--metrics']
        child=None;error=None;began=time.monotonic()
        try:
            with (folder/'native.log').open('ab')as log:
                child=subprocess.Popen(command,cwd=runner.parent,stdout=log,stderr=subprocess.STDOUT,**d.basic.assets.HIDDEN)
                import psutil
                write(folder/'worker.json',{'pid':child.pid,'create_time':psutil.Process(child.pid).create_time(),'command':command},immutable=True)
                while child.poll() is None:
                    write(output/'worker.json',{'stage':'native','pid':child.pid,'session':sid,'unit':unit['id'],
                        'attempt':ordinal,'elapsed_seconds':round(time.monotonic()-began,2),'updated_unix':time.time(),**d.UNCHECKED})
                    if time.monotonic()-began>900:raise TimeoutError('Bounded technical native timeout')
                    time.sleep(1)
                if child.returncode:error='Native exit '+str(child.returncode)
        except (OSError,TimeoutError,KeyboardInterrupt)as problem:
            error=type(problem).__name__+': '+str(problem)
            if isinstance(problem,KeyboardInterrupt):raise
        finally:
            if child is not None and child.poll() is None:child.kill();child.wait()
            write(folder/'physical-result.json',{'returncode':child.returncode if child else None,'error':error},immutable=True)
    terminal=read(folder/'physical-result.json');source=folder/(unit['id']+'.wav')
    row={'unit':unit['id'],'session':sid,'take':unit['retake_number'],'technical_attempt':ordinal,
        'synthesis_sha256':digest(unit['synthesis']),'request_sha256':digest(request),'effective_unit':unit,**d.UNCHECKED}
    try:
        if terminal['returncode']!=0:raise ValueError('Native process did not finish successfully: '+str(terminal))
        row['format']=d.basic.basic_audio(source)
        if row['format']['duration']*unit['synthesis'].get('codec_frame_rate',25)>=(unit['synthesis']['max_tokens']-8)*.98:
            raise ValueError('Output reached decoder ceiling')
        sha=file_digest(source);target=output/'assets'/(sha+'.wav');copy_exact(source,target,sha)
        row.update(status='rendered',audio=target.relative_to(output).as_posix(),audio_sha256=sha)
    except (OSError,ValueError,RuntimeError)as problem:row.update(status='technical_failure',error=str(problem))
    write(folder/'unit-result.json',row,immutable=True)
    return row,folder


def render_unit(output,ready,unit):
    receipt=output/'units'/(unit['id']+'.json')
    if receipt.exists():
        row=read(receipt);effective=row.get('effective_unit',unit)
        validate_effective(unit,effective)
        if row.get('synthesis_sha256')!=digest(effective['synthesis']):raise ValueError('Existing correction receipt binding changed')
        return row,effective
    current=copy.deepcopy(unit);seed_changed=False
    for ordinal in range(1,4):
        row,folder=native_attempt(output,ready,current,ordinal)
        if row['status']=='rendered':write(receipt,row,immutable=True);return row,current
        log=(folder/'native.log').read_text('utf-8',errors='replace') if (folder/'native.log').exists() else ''
        cap='reached max_tokens' in log or 'decoder ceiling' in row.get('error','')
        if not cap:break
        if current['synthesis']['max_tokens']<2048:
            old_cap=current['synthesis']['max_tokens'];current['synthesis']['max_tokens']=2048
            current['synthesis']['token_cap']={'policy':POLICY,'original':copy.deepcopy(unit['synthesis'].get('token_cap')),
                'original_max_tokens':old_cap,'effective_max_tokens':2048,'action':'raised_to_2048'}
        elif not seed_changed:
            current['synthesis']['sampling']['seed']=(current['synthesis']['sampling']['seed']+1)%(2**31);seed_changed=True
        else:break
        current['synthesis']['listener_technical_recovery']={'policy':POLICY,'previous_session':row['session'],'attempt':ordinal+1,
            'reason':'Terminal missing-output decoder failure; no quality adjudication'}
        current['key']=digest(current['synthesis'])
        validate_effective(unit,current)
    write(receipt,row,immutable=True);return row,current


def publish(output,ready,units,data_dir,status=None,error=None):
    from higgs_published_timeline import pin_timeline
    for cid in ready['chapters']:
        d.publish(output,ready,cid,units,None)
        if not (output/'progress.json').exists():continue
        state=read(output/'progress.json');chapter=state['chapters'].get(cid)
        if not chapter:continue
        rows=read(output/chapter['timeline_asset']['file']);by_id={u['id']:u for u in units}
        for row in rows:
            unit=by_id[row['id']];receipt=read(output/'units'/(row['id']+'.json'))
            if unit['synthesis']['backend']!='nonverbal_asset' and receipt['synthesis_sha256']!=digest(unit['synthesis']):
                raise ValueError('Published audio differs from effective human correction input')
            row['synthesis']=unit['synthesis']
        pin_timeline(output,cid,chapter,rows);write(output/'progress.json',state)
    state=read(output/'progress.json')
    state['limitations']=['Human stress correction applied; audio accuracy, recognition and listening approval were not checked.']
    if status:state.update(status=status,error=error or '')
    write(output/'progress.json',state)
    from sync_higgs_preview_runtime import sync_preview
    return sync_preview(workspace=ROOT,source=ROOT/'content'/read(output.parent/'source/book.json')['filename'],
        prepared_book=output.parent/'source/book.json',generation_dir=output,data_dir=data_dir)


def complete_publication(output,ready,synced):
    state=read(output/'progress.json')
    if state.get('status')!='complete' or synced['status']!='ready' or synced['segments']!=ready['unit_count']:
        raise ValueError('Corrected publication is incomplete')
    for cid,count in ready['chapter_counts'].items():
        chapter=state['chapters'][cid]
        if not chapter.get('complete') or chapter['units']!=count or chapter.get('music_pending'):
            raise ValueError('Corrected chapter coverage/music incomplete: '+cid)
        if not any(m.get('kind')=='scene_bed' for m in chapter.get('music',[])):
            raise ValueError('Corrected chapter scene music missing: '+cid)
    if not any(m.get('kind')=='ending' for m in state['chapters'][ready['chapters'][-1]].get('music',[])):
        raise ValueError('Corrected final chapter ending music missing')


def record_note_outcomes(output,config,result,data_dir):
    """Publish note dispositions and consume the single coalesced queue job."""
    from rimsky.util import now
    coverage=read(output/'listener-note-coverage.json');by_note={r['report_id']:r for r in coverage['notes']}
    terminal_sha=file_digest(output/'listener-repair-terminal.json');coverage_sha=file_digest(output/'listener-note-coverage.json')
    store=Store(data_dir,correction_profile=listener_stress.shared_profile())
    try:
        generation=store.generation(result['generation_id']);book=store.book(generation['book_id'])
        canonical={x['report']['id']:x['report'] for x in listener_stress.notes(store,book)}
        if generation['status']!='ready' or len(generation['segments'])!=config['unit_count']:
            raise ValueError('Note outcomes require complete playable production-reader coverage')
        segments={s['id']:s for s in generation['segments']};outcomes=[]
        for identifier,row in by_note.items():
            if row['status'] not in ('applied','nochange'):continue
            original=canonical[identifier];targets=[]
            for uid in row['current_units']:
                segment=segments[uid];receipt=read(output/'units'/(uid+'.json'))
                d.basic.checked(output/receipt['audio'],receipt['audio_sha256'])
                d.basic.checked(data_dir/'audio'/(segment['audio_hash']+'.wav'),segment['audio_hash'])
                if receipt['status']!='rendered' or segment['status']!='ready':raise ValueError('Note outcome target is not playable')
                targets.append({k:segment[k] for k in ('id','source_id','start','end','audio_hash')})
            if not targets:raise ValueError('Applied note lacks current source targets')
            receipt={'schema':2,'id':identifier+'-shared-publication-'+generation['id'],
                'generation_id':generation['id'],'report':original,'status':'applied','error':'',
                'created':now(),'updated':now(),'completed':now(),'targets':targets,
                'native_input_disposition':row['status'],'coverage_sha256':coverage_sha,'terminal_sha256':terminal_sha,
                'published_generation_id':generation['id'],'affected_segment_ids':[x['id'] for x in targets],
                'affected_source_ids':sorted({x['source_id'] for x in targets}),
                'human_audio_approval':False,**d.UNCHECKED}
            path=output/'listener-stress-queue'/(receipt['id']+'.json')
            if path.exists():
                previous=read(path)
                for key in ('created','updated','completed'):receipt[key]=previous[key]
            write(path,receipt,immutable=True);outcomes.append({'file':path.name,'sha256':file_digest(path)})
        for queued in config['coalesced_queue_items']:
            path=Path(queued['path']);item=read(path);origin=item.get('origin_report') or item.get('original_report') or item['report']
            if item['id']!=queued['id'] or digest(origin)!=queued['origin_report_sha256']:
                raise ValueError('Coalesced queue origin changed before delivery')
            disposition=by_note[origin['id']]
            if disposition['status']=='unbound' and not disposition['reason'].startswith('Superseded'):
                raise ValueError('Coalesced report remains unbound: '+origin['id'])
            item.update(status='applied',phase='delivered',error='',updated=now(),completed=now(),
                result=result,result_sha256=file_digest(output/'listener-repair-terminal.json'),
                native_input_disposition=disposition['status'],disposition_reason=disposition['reason'],
                coverage_sha256=coverage_sha,human_audio_approval=False)
            item.update(published_generation_id=generation['id'],affected_segment_ids=result['affected_ids'],
                affected_source_ids=result['affected_source_ids'])
            write(path,item)
        write(output/'listener-note-outcomes.json',{'generation_id':generation['id'],'count':len(outcomes),
            'files':outcomes,'terminal_sha256':terminal_sha,'consumed_queue_items':config['coalesced_queue_items']},immutable=True)
    finally:store.close()


def require_pending_coverage(output,config):
    coverage={r['report_id']:r for r in read(output/'listener-note-coverage.json')['notes']}
    for origin in config['pending_origins']:
        row=coverage.get(origin['id'])
        if row is None or (row['status']=='unbound' and not row['reason'].startswith('Superseded')):
            raise ValueError('Pending human correction remains unbound: '+origin['id'])


def run(args):
    base=args.generation.resolve();item={**read(args.queue_item),'_queue_item_path':str(args.queue_item.resolve())}
    output,config,ready,original,effective,blocks,frozen,notes=configure(base,args.data_dir,args.generation_id,item)
    with d.basic.exclusive(output):
        config,new_ready=materialize(output,config,ready,original,effective,blocks,frozen,notes)
        if args.configure_only:
            result={'status':'configured','generation':str(output),'affected_ids':config['affected_ids'],'reused':config['reused']}
            write(args.result,result);print(json.dumps(result),flush=True);return 0
        failed=[];selected=[]
        try:
            by_id={u['id']:u for u in effective};selected=list(effective)
            pending=sorted(config['affected_ids'],key=lambda uid:(uid!=config['priority_unit'],list(by_id).index(uid)))
            for index,uid in enumerate(pending):
                unit=by_id[uid]
                if unit['id'] in config['affected_ids']:
                    d.basic.checked(SELF,config['helper_sha256']);d.basic.checked(output/'ready.json',config['derived_ready_sha256'])
                    d.basic.checked(Path(listener_stress.__file__),config['listener_module_sha256'])
                    row,unit=render_unit(output,new_ready,unit)
                    if row['status']!='rendered':failed.append(unit['id'])
                    print(json.dumps({'unit':unit['id'],'status':row['status'],'error':row.get('error'),
                        'finished':index+1,'total':len(pending)}),flush=True)
                by_id[unit['id']]=unit;selected=[by_id[u['id']] for u in effective]
                if (index+1)%8==0:
                    publish(output,new_ready,selected,args.data_dir,'technical_failure' if failed else None,
                        ', '.join(failed) if failed else None)
                    handoff_gateway(output,config,new_ready,args.data_dir)
            # Technical fallback effective requests are recorded separately, not
            # written over the first immutable correction plans/attempts.
            write(output/'effective-listener-units.json',{'units':selected},immutable=True)
            synced=publish(output,new_ready,selected,args.data_dir,'technical_failure' if failed else None,
                'Bounded technical listener correction held: '+', '.join(failed) if failed else None)
            if not failed:complete_publication(output,new_ready,synced)
            if not failed:require_pending_coverage(output,config)
            gateway=handoff_gateway(output,config,new_ready,args.data_dir,final=not failed)
            if inventory(base)!=config['base_inventory']:raise ValueError('Original generation bytes changed during listener repair')
            result={'status':'held' if failed else 'applied','generation':str(output),'generation_name':output.name,
                'affected_ids':config['affected_ids'],'reused':config['reused'],'rendered':len(config['affected_ids'])-len(failed),
                'generation_id':synced['generation_id'],
                'published_generation_id':synced['generation_id'],'affected_segment_ids':config['affected_ids'],
                'affected_source_ids':sorted({u['source_id'] for u in selected if u['id'] in config['affected_ids']}),
                'gateway':gateway,
                'covered_report_ids':[r['id'] for r in config['pending_origins']],
                'failures':failed,'source_preserved':True,'note_coverage':str(output/'listener-note-coverage.json'),**d.UNCHECKED}
            write(output/'listener-repair-terminal.json',result,immutable=True)
            if not failed:record_note_outcomes(output,config,result,args.data_dir)
            write(args.result,result)
            write(output/'worker.json',{'stage':'stopped',**result,'updated_unix':time.time()})
            print(json.dumps(result),flush=True);return int(bool(failed))
        except BaseException as error:
            if (output/'progress.json').exists():
                try:publish(output,new_ready,selected or effective,args.data_dir,'technical_failure',str(error))
                except Exception:pass
            write(output/'worker.json',{'stage':'stopped','error':str(error),'updated_unix':time.time(),**d.UNCHECKED})
            raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ('generation','data-dir','queue-item','result'):parser.add_argument('--'+flag,type=Path,required=True)
    parser.add_argument('--generation-id',required=True);parser.add_argument('--configure-only',action='store_true')
    return run(parser.parse_args())


if __name__=='__main__':raise SystemExit(main())
