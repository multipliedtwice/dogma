from __future__ import annotations

import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'scripts'))
from rimsky.higgs_backend import HiggsBackend, gguf_precision, synthesis_plan
from rimsky.jobs import Jobs, render_controls, render_key
from rimsky.analysis import source_units
from rimsky.packs import load_pack
from rimsky.store import Store
from rimsky.util import AppError, SpeechHoldError, digest, file_digest, write_json
from test_core import FB2, SyntheticEngine


def tiny_gguf(path):
    key = b'general.file_type'
    path.write_bytes(b'GGUF' + struct.pack('<IQQQ', 3, 0, 1, len(key)) + key + struct.pack('<II', 4, 7))


def protocol_jobs(jobs):
    """Isolate cache/retake unit tests from scene and listening qualification.

    The complete pipeline/gate test below deliberately supplies no approvals
    and verifies that candidate audio cannot be released.
    """
    jobs._character_direction=lambda gid,book,cast,arguments: {'profiles':{c['id']:{} for c in cast}}
    jobs._scene_direction=lambda *args:None
    jobs._chapter_acceptance=lambda gid,*args:jobs.store.execute(
        "UPDATE segments SET status='ready' WHERE generation_id=? AND status='pending' AND audio_hash IS NOT NULL",(gid,))
    return jobs


def protocol_frame(payload):
    targets=payload['request']['targets']
    evidence=next((t for t in targets if t['kind'] not in ('heading','separator')),targets[0])
    return {'ranges':[{'first':targets[0]['id'],'last':targets[-1]['id'],'role':'main_story',
        'register':'ordinary','perspective':'third_person','focal_identity':'fixture','relation_to_main':'Main story',
        'confidence':'high','reason':'Fixture narrative.','evidence':[{'id':evidence['id'],'quote':evidence['text']}]}]}


def fixture(root,purpose='evaluation_only'):
    root.mkdir()
    (root / 'speech').mkdir()
    (root / 'verifier').mkdir()
    (root / 'verifier/config.json').write_text('{}')
    (root / 'text.gguf').write_bytes(b'fixture')
    tiny_gguf(root / 'speech/model.gguf')
    (root / 'speech/runner.exe').write_bytes(b'fixture')
    audio = np.r_[np.zeros(1200),np.sin(np.arange(24000) * .05).astype('float32') * .1,np.zeros(1200)]
    sf.write(root / 'reference.wav', audio, 24000, subtype='PCM_16')
    mapping = {'tags': [], 'reviewed': True, 'listening_sha256': 'a' * 64}
    voices = [{'id': vid, 'name': vid, 'role': role, 'family': vid,
               'references': {'neutral': {'audio': 'reference.wav', 'text': 'Reference.', 'reviewed': True,
                                          'voice_family': vid, 'review_sha256': 'b' * 64}},
               'higgs_controls': {'moods': {'neutral': dict(mapping), 'excited': {**mapping, 'tags': ['enthusiasm']}},
                                 'paces': {pace: dict(mapping) for pace in ('normal', 'deliberate', 'brisk')}}}
              for vid, role in (('n', 'narrator'), ('c', 'character'))]
    reviews = [{'voice_family': v['id'], 'control_type': kind, 'control_id': cid, 'tags': mapping['tags'],
                'reference_sha256': file_digest(root / 'reference.wav'), 'verdict': 'approved'}
               for v in voices for kind, table in (('mood', v['higgs_controls']['moods']), ('pace', v['higgs_controls']['paces']))
               for cid, mapping in table.items()]
    write_json(root / 'speech/listening.json', {'model_sha256': file_digest(root / 'speech/model.gguf'), 'reviews': reviews})
    for v in voices:
        for table in v['higgs_controls'].values():
            for mapping in table.values():
                mapping['listening_sha256'] = file_digest(root / 'speech/listening.json')
    write_json(root / 'speech/training.json', {'kind': 'lora', 'optimizer_steps': 10, 'dataset_sha256': 'c' * 64,
                                             'checkpoint_sha256': 'd' * 64, 'model_sha256': file_digest(root / 'speech/model.gguf')})
    pack = {'schema': 1, 'name': 'Higgs fixture', 'text_model': {'path': 'text.gguf', 'context': 8192}, 'voices': voices,
            'speech': {'path': 'speech', 'variant': 'clone', 'backend': 'higgs_audio_cpp', 'model': 'speech/model.gguf',
                       'runner': 'speech/runner.exe', 'training_provenance': 'speech/training.json', 'precision': 'q8_0',
                       'purpose': purpose,
                       'effects_policy':'qualified',  # these fixtures exercise already-qualified control tuples
                       'listening_review': 'speech/listening.json',
                       'runner_version': 'fixture runner', 'threads': 2,
                       'verifier': {'backend': 'qwen3_asr', 'path': 'verifier'},
                       'sampling': {'seed': 42, 'temperature': .8, 'top_k': 50, 'top_p': .95, 'repetition_penalty': 1.1, 'do_sample': True}}}
    pack['files'] = {p.relative_to(root).as_posix(): file_digest(p) for p in root.rglob('*') if p.is_file()}
    write_json(root / 'pack.json', pack)
    return load_pack(str(root), verify=True)


class HiggsTests(unittest.TestCase):
    def test_production_key_uses_preposition_stress_and_respects_explicit_marks(self):
        text='Охотник резко вскочил на ноги и вскинул лук.'
        marked='Охотник резко вскочил на́ ноги и вскинул лук.'
        voice=self.pack['voices'][0];config={'resolved_speech':self.resolved}
        expected=synthesis_plan(self.pack,voice,marked,'neutral',None,self.resolved,0)
        key=render_key(self.pack,config,text,voice,'neutral','Russian',0)
        self.assertEqual(key,digest({'schema':5,'language':'Russian','plan':expected}))
        self.assertEqual(key,render_key(self.pack,config,marked,voice,'neutral','Russian',0))
        explicit='Охотник резко вскочил на но́ги и вскинул лук.'
        self.assertNotEqual(key,render_key(self.pack,config,explicit,voice,'neutral','Russian',0))

    def test_native_request_and_cached_word_audit_use_the_same_phrase_stress(self):
        from rimsky.speech_quality import transcript_check
        from rimsky.spoken_pronunciation import unmarked
        text='Охотник резко вскочил на ноги и вскинул лук.'
        voice=self.pack['voices'][0];wav=self.root/'take.wav';seen=[]
        prefix=self.pack['speech']['verifier']['path'].rstrip('/')+'/'
        verifier=digest({k:v for k,v in self.pack['files'].items() if k.startswith(prefix)})
        def guarded(method,payload):
            seen.append((method,payload['text']))
            if method=='render':
                sf.write(wav,np.r_[np.zeros(1200),np.sin(np.arange(24000)*.05)*.1,np.zeros(1200)],24000)
                planned=synthesis_plan(self.pack,voice,payload['text'],'neutral',None,self.resolved,0)
                return {'synthesis':{**planned,'plan_sha256':digest(planned)},'audio_checks':{}}
            return transcript_check(payload['text'],unmarked(payload['text']),file_digest(wav),verifier)
        jobs=Jobs.__new__(Jobs);jobs._guarded=guarded
        request={'post_processing':'on','pack':self.pack,'text':text,'voice_id':voice['id'],'delivery':'neutral',
                 'language':'Russian','resolved_speech':self.resolved,'take':0}
        result=jobs._speak(request,wav)
        self.assertTrue(all('на́ ноги' in native for _,native in seen))
        config={'resolved_speech':self.resolved,'_speech_language':'Russian','post_processing':'on'}
        key=render_key(self.pack,config,text,voice,'neutral','Russian',0)
        plans=[({'text':text},voice,'neutral',True,'',key)]
        self.assertTrue(jobs._higgs_cached(result,plans,self.pack,config))
        self.assertEqual(result['phrase_pronunciation']['policy'],'ru-feet-preposition-v1')
    def test_default_assignment_policy_uses_reference_without_higgs_effects(self):
        pack=fixture(self.root/'reference-first',purpose='production')
        pack['speech'].pop('effects_policy')
        voice=pack['voices'][0]
        plain=synthesis_plan(pack,voice,'A sentence.','neutral',None,self.resolved)
        self.assertEqual(plain['prompt'],'A sentence.')
        self.assertEqual(plain['effects_policy'],'disabled')
        with self.assertRaisesRegex(SpeechHoldError,'effects are disabled'):
            synthesis_plan(pack,voice,'A sentence.','excited',None,self.resolved)
        pack['speech']['effects_policy']='qualification'
        with self.assertRaisesRegex(SpeechHoldError,'qualification audition'):
            synthesis_plan(pack,voice,'A sentence.','excited',None,self.resolved)

    def test_production_reference_with_active_edge_is_rejected_before_control_approval(self):
        from rimsky.higgs_backend import validate_higgs_pack
        pack=fixture(self.root/'reference-edge',purpose='production')
        root=Path(pack['_root']);sf.write(root/'reference.wav',np.full(24000,.1),24000)
        pack['files']['reference.wav']=file_digest(root/'reference.wav')
        with self.assertRaisesRegex(SpeechHoldError,'active audio edge'):
            validate_higgs_pack(pack,root)

    def test_production_mood_cannot_recast_character_through_another_reference(self):
        from rimsky.higgs_backend import synthesis_plan
        from rimsky.util import SpeechHoldError
        import copy
        pack=fixture(self.root/'stable-production',purpose='production');voice=pack['voices'][0]
        voice['references']['other']={**voice['references']['neutral'],'audio':'other.wav'}
        pack['files']['other.wav']='f'*64
        voice['higgs_controls']['moods']['excited']['reference']='other'
        with self.assertRaisesRegex(SpeechHoldError,'frozen character reference'):
            synthesis_plan(pack,voice,'A sentence.','excited',None,self.resolved)

    def test_production_requires_the_actual_combined_control_prefix(self):
        from rimsky.higgs_backend import synthesis_plan
        from rimsky.util import SpeechHoldError
        pack=fixture(self.root/'combined-production',purpose='production')
        voice=pack['voices'][0]
        voice['higgs_controls']['paces']['deliberate']['tags']=['speed_slow']
        path=Path(pack['_root'])/pack['speech']['listening_review']
        review=json.loads(path.read_text(encoding='utf-8'))
        for row in review['reviews']:
            if row['voice_family']==voice['id'] and row['control_type']=='pace' and row['control_id']=='deliberate':
                row['tags']=['speed_slow']
        write_json(path,review);pack['files'][pack['speech']['listening_review']]=file_digest(path)
        with self.assertRaisesRegex(SpeechHoldError,'combined'):
            synthesis_plan(pack,voice,'A sentence.','excited',{'pace':'deliberate'},self.resolved)
        review['reviews'].append({'voice_family':voice['id'],'control_type':'combination',
            'tags':['enthusiasm','speed_slow'],'reference_sha256':pack['files']['reference.wav'],'verdict':'approved'})
        write_json(path,review);pack['files'][pack['speech']['listening_review']]=file_digest(path)
        plan=synthesis_plan(pack,voice,'A sentence.','excited',{'pace':'deliberate'},self.resolved)
        self.assertTrue(plan['prompt'].startswith('<|emotion:enthusiasm|><|prosody:speed_slow|>'))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pack = fixture(self.root / 'pack')
        self.resolved = {'backend': 'higgs_audio_cpp', 'device': 'cpu', 'device_index': 0,
                         'precision': 'q8_0', 'device_name': 'cpu', 'runner_version': 'fixture runner', 'implementation_sha256': 'e' * 64}

    def key(self, pack=None, config=None, take=0):
        pack = pack or self.pack
        return render_key(pack, config or {'device': 'auto', 'resolved_speech': self.resolved}, 'A sentence.',
                          pack['voices'][0], 'excited', 'English', take)

    def test_actual_precision_and_custom_checkpoint_proof(self):
        self.assertEqual('q8_0', gguf_precision(self.root / 'pack/speech/model.gguf'))
        manifest = json.loads((self.root / 'pack/pack.json').read_text())
        manifest['speech']['precision'] = 'q4_0'
        write_json(self.root / 'pack/pack.json', manifest)
        with self.assertRaisesRegex(AppError, 'precision'):
            load_pack(str(self.root / 'pack'))
        manifest['speech']['precision'] = 'q8_0'
        write_json(self.root / 'pack/speech/training.json', {'kind': 'stock'})
        manifest['files']['speech/training.json'] = file_digest(self.root / 'pack/speech/training.json')
        write_json(self.root / 'pack/pack.json', manifest)
        with self.assertRaisesRegex(AppError, 'custom fine-tune'):
            load_pack(str(self.root / 'pack'))

    def test_probe_pins_the_runner_device_view(self):
        def cli(command, **kwargs):
            output = 'fixture runner' if '--version' in command else 'CUDA:0 "actual GPU" [GPU]\r\nCPU:0 "actual CPU" [CPU]\r\n'
            return types.SimpleNamespace(returncode=0, stdout=output, stderr='')
        with patch('rimsky.higgs_backend.run_cli', cli):
            resolved = HiggsBackend().probe({'pack': self.pack, 'device': 'auto'})
        self.assertEqual(('cuda', 'actual GPU', 'q8_0'), tuple(resolved[k] for k in ('device', 'device_name', 'precision')))

    def test_backend_fingerprint_survives_frozen_install_paths(self):
        from rimsky.speech_backend import implementation_fingerprint
        def fingerprint(code):
            loader = types.SimpleNamespace(get_code=lambda _: code)
            with patch('rimsky.speech_backend.importlib.util.find_spec', return_value=types.SimpleNamespace(loader=loader)):
                return implementation_fingerprint('fixture')
        self.assertEqual(fingerprint(compile('def render(): return 42', 'core/path.py', 'exec')),
                         fingerprint(compile('def render(): return 42', 'infer/path.py', 'exec')))
        self.assertNotEqual(fingerprint(compile('def render(): return 42', 'path.py', 'exec')),
                            fingerprint(compile('def render(): return 43', 'path.py', 'exec')))

    def test_sound_inputs_and_resolved_device_change_key(self):
        original = self.key()
        for field, value in (('seed', 7), ('temperature', .5), ('do_sample', False)):
            modified = copy.deepcopy(self.pack)
            modified['speech']['sampling'][field] = value
            self.assertNotEqual(original, self.key(modified))
        changed = {**self.resolved, 'device': 'cuda', 'device_name': 'fixture GPU'}
        self.assertNotEqual(original, self.key(config={'device': 'auto', 'resolved_speech': changed}))
        self.assertNotEqual(original, self.key(take=1))
        modified = copy.deepcopy(self.pack)
        modified['voices'][0]['references']['neutral']['text'] = 'Different reference.'
        self.assertNotEqual(original, self.key(modified))

    def test_no_reference_or_control_fallback(self):
        plan = synthesis_plan(self.pack, self.pack['voices'][0], 'Hello.', 'excited', {}, self.resolved, 2)
        self.assertEqual('<|emotion:enthusiasm|>Hello.', plan['prompt'])
        self.assertEqual(44, plan['sampling']['seed'])
        with self.assertRaises(SpeechHoldError):
            synthesis_plan(self.pack, self.pack['voices'][0], 'Hello.', 'disgusted', {}, self.resolved)
        voice = copy.deepcopy(self.pack['voices'][0])
        voice['references'] = {}
        with self.assertRaises(SpeechHoldError):
            synthesis_plan(self.pack, voice, 'Hello.', 'neutral', {}, self.resolved)

    def test_scene_meaning_never_overwrites_audible_delivery(self):
        controls, unsupported = render_controls(self.pack, {'text': 'Hello!', 'delivery': 'excited', 'speaker': 'n',
                                                            'performance': {'schema': 2, 'mood': 'neutral', 'pace': 'normal'}}, {})
        self.assertEqual('excited', controls['mood'])
        self.assertFalse(unsupported)

    def test_cli_uses_pinned_controls_and_rejects_silence_atomically(self):
        payload = {'pack': self.pack, 'device': 'cpu', 'resolved_speech': self.resolved, 'voice_id': 'n',
                   'text': 'Hello.', 'delivery': 'excited', 'take': 3, 'output': str(self.root / 'out.wav')}
        commands = []
        def cli(command, **kwargs):
            commands.append(command)
            destination = Path(command[command.index('--out') + 1])
            sf.write(destination, np.zeros(24000), 24000, subtype='PCM_16')
            return types.SimpleNamespace(returncode=0, stderr='', stdout='')
        backend = HiggsBackend()
        with patch.object(backend, 'probe', return_value=self.resolved), patch('rimsky.higgs_backend.run_cli', cli):
            with self.assertRaises(SpeechHoldError):
                backend.render(payload)
        self.assertFalse(Path(payload['output']).exists())
        cmd = commands[0]
        self.assertEqual('45', cmd[cmd.index('--seed') + 1])
        self.assertEqual('<|emotion:enthusiasm|>Hello.', cmd[cmd.index('--text') + 1])
        self.assertNotIn('--batch-text-dir', cmd)

    def test_sentence_units_preserve_internal_parts(self):
        from test_core import NEUTRAL_CONTEXT
        text = 'Первое предложение. Второе предложение.'
        source = {'id': 'p', 'source_id': 'p', 'start': 0, 'ordinal': 0, 'chapter_id': 'c', 'text': text}
        part = {**source, 'end': len(text), 'speaker': 'narrator', 'delivery': 'neutral', 'context': NEUTRAL_CONTEXT, 'evidence': []}
        units = source_units(source, [part], sentence_sized=True)
        self.assertEqual(2, len(units))
        self.assertEqual(text, ''.join(u['text'] for u in units))
        self.assertTrue(all(len(u['performance']['analysis_parts']) == 1 for u in units))

    def test_generation_recomputes_hash_after_resolution_changes(self):
        store = Store(self.root / 'library')
        self.addCleanup(store.close)
        store.update_settings({'cast_review': False, 'device': 'cpu'})
        book = store.import_data(FB2, 'fixture.fb2')
        generation = store.create_generation(book['id'], {'pack_path': self.pack['_root'], 'pack_fingerprint': self.pack['_fingerprint'],
                                                         'device': 'cpu', 'align_words': False, 'post_processing':'on'})
        test = self
        class Engine(SyntheticEngine):
            def call(self, method, payload, stop):
                if method=='narrative_frame':return protocol_frame(payload)
                if method == 'probe':
                    return dict(test.resolved)
                if method == 'verify_audio':
                    from rimsky.speech_quality import transcript_check
                    signature = digest({k: v for k, v in payload['pack']['files'].items() if k.startswith('verifier/')})
                    return transcript_check(payload['text'], payload['text'], file_digest(Path(payload['audio'])), signature)
                if method == 'render':
                    result = super().call(method, payload, stop)
                    voice = next(v for v in payload['pack']['voices'] if v['id'] == payload['voice_id'])
                    plan = synthesis_plan(payload['pack'], voice, payload['text'], payload['delivery'], payload.get('controls'),
                                          payload['resolved_speech'], payload.get('take', 0))
                    return {**result, 'synthesis': {**plan, 'plan_sha256': digest(plan)}}
                return super().call(method, payload, stop)
        def run():
            engine = Engine()
            jobs = protocol_jobs(Jobs(store, self.root, engine_factory=lambda _: engine))
            jobs.start(generation['id'])
            jobs.thread.join(10)
            self.assertFalse(jobs.thread.is_alive())
            jobs.shutdown()
            return engine, store.generation(generation['id'])
        first_engine, first = run()
        self.assertEqual('ready', first['status'], first['error'])
        before = [s['audio_hash'] for s in first['segments']]
        store.status(generation['id'], 'failed')
        cached_engine, cached = run()
        self.assertEqual(0, cached_engine.rendered)
        meta = store.root / 'audio' / (cached['segments'][0]['audio_hash'] + '.json')
        receipt = json.loads(meta.read_text('utf8'))
        for field in ('word_check', 'word_checks_parts'):
            receipt.pop(field, None)
        write_json(meta, receipt)
        for part_key in cached['segments'][0]['performance'].get('render_parts', []):
            part_meta = store.root / 'audio' / (part_key + '.json')
            part_receipt = json.loads(part_meta.read_text('utf8'))
            part_receipt.pop('word_check', None)
            write_json(part_meta, part_receipt)
        store.status(generation['id'], 'failed')
        repaired_engine, repaired = run()
        self.assertGreater(repaired_engine.rendered, 0)
        self.assertEqual('ready', repaired['status'], repaired['error'])
        self.resolved = {**self.resolved, 'device_name': 'different CPU implementation'}
        store.status(generation['id'], 'failed')  # resume a generation with stored ready takes
        engine, after = run()
        self.assertGreater(engine.rendered, 0)
        self.assertNotEqual(before, [s['audio_hash'] for s in after['segments']])
        self.assertEqual(self.resolved, after['config']['resolved_speech'])
        target = after['segments'][0]
        record = {'target_id': target['id'], 'source_id': target['source_id'], 'start': target['start'], 'end': target['end'],
                  'speaker': target['speaker'], 'primary_speaker': target['speaker'], 'tier': 'backend',
                  'triggers': ['render_degraded'], 'status': 'review', 'reason': 'Provisional take.'}
        store.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps({**after['analysis'], 'speaker_resolution': [record]}), generation['id']))
        store.status(generation['id'], 'failed')
        resumed_engine, resumed = run()
        self.assertEqual('held', resumed['status'])
        # The explicit degraded-take hold now prevents chapter publication.
        # Its cached WAV is retained, but isn't a production-ready segment.
        self.assertEqual('review', resumed['segments'][0]['status'])
        self.assertEqual(target['audio_hash'], resumed['segments'][0]['audio_hash'])
        self.assertEqual(0, resumed_engine.rendered)

    def test_listening_approval_is_bound_to_model_voice_reference_and_tags(self):
        path = self.root / 'pack/pack.json'
        pack = json.loads(path.read_text('utf8'))
        pack['voices'][0]['higgs_controls']['moods']['excited']['tags'] = ['anger']
        write_json(path, pack)
        with self.assertRaisesRegex(AppError, 'matching listening approval'):
            load_pack(str(path.parent))

    def test_spoken_word_gate_recomputes_instead_of_trusting_status(self):
        from rimsky.speech_quality import transcript_check, word_tokens
        self.assertEqual(word_tokens('Кто-то'), word_tokens('кто то'))
        self.assertNotEqual(word_tokens('Кто-то'), word_tokens('ктото'))
        store = Store(self.root / 'library')
        self.addCleanup(store.close)
        jobs = Jobs(store, self.root)
        self.addCleanup(jobs.shutdown)
        request = {'post_processing':'on','pack': self.pack, 'device': 'cpu', 'resolved_speech': self.resolved, 'voice_id': 'n',
                   'text': 'Hello.', 'delivery': 'neutral', 'language': 'English'}
        wav = self.root / 'output.wav'
        def guarded(method, payload):
            if method == 'render':
                sf.write(wav, .1 * np.sin(np.arange(24000) * .05), 24000, subtype='PCM_16')
                plan = synthesis_plan(self.pack, self.pack['voices'][0], 'Hello.', 'neutral', {}, self.resolved)
                return {'warning': '', 'synthesis': {**plan, 'plan_sha256': digest(plan)}}
            signature = digest({k: v for k, v in self.pack['files'].items() if k.startswith('verifier/')})
            audit = transcript_check('Hello.', 'Wrong words.', file_digest(wav), signature)
            return {**audit, 'status': 'asr_exact_match'}  # a claimed match is insufficient
        jobs._guarded = guarded
        with self.assertRaises(SpeechHoldError):
            jobs._speak(request, wav)
        self.assertFalse(wav.exists())

    def test_automatic_quality_retake_changes_only_take_and_keeps_provenance(self):
        jobs=Jobs.__new__(Jobs);jobs._check=lambda:None;jobs._intact=lambda *args:None
        jobs.store=types.SimpleNamespace(root=self.root)
        requests=[]
        def speak(request,wav):
            requests.append(copy.deepcopy(request))
            if len(requests)==1:raise SpeechHoldError('Word check mismatch')
            return {'duration':1.2,'warning':'','synthesis':{'take':request['take']}}
        jobs._speak=speak
        request={'post_processing':'on','pack':self.pack,'take':7,'text':'Hello there.','voice_id':'n','delivery':'excited',
                 'controls':{'mood':'excited'},'language':'English','resolved_speech':self.resolved}
        result=jobs._synthesize('g',{},request,self.root/'candidate.wav')
        self.assertEqual([7,8],[r['take'] for r in requests])
        self.assertEqual({k:v for k,v in requests[0].items() if k!='take'},
                         {k:v for k,v in requests[1].items() if k!='take'})
        self.assertEqual(8,result['automatic_retake']['selected_take'])
        self.assertEqual(['Word check mismatch'],result['automatic_retake']['failures'])

    def test_immutable_cache_collision_advances_seed_without_overwriting(self):
        jobs=Jobs.__new__(Jobs);jobs.store=types.SimpleNamespace(root=self.root)
        jobs._check=lambda:None;jobs._intact=lambda *args:None
        request={'post_processing':'on','pack':self.pack,'take':0,'text':'Hello there.','voice_id':'n',
            'delivery':'excited','language':'English','resolved_speech':self.resolved}
        key=render_key(self.pack,request,request['text'],self.pack['voices'][0],'excited','English',0)
        (self.root/'audio').mkdir();published=self.root/'audio'/f'{key}.wav';published.write_bytes(b'older immutable take')
        attempts=[]
        def speak(candidate,wav):
            attempts.append(candidate['take']);wav.write_bytes(b'new candidate')
            return {'duration':1.2,'warning':''}
        jobs._speak=speak
        result=jobs._synthesize('g',{},request,self.root/'candidate.wav')
        self.assertEqual([0,1],attempts);self.assertEqual(1,result['automatic_retake']['selected_take'])
        self.assertEqual(b'older immutable take',published.read_bytes())

    def test_machine_word_holds_retry_automatically_but_label_holds_remain(self):
        store=Store(self.root/'recovery-library');self.addCleanup(store.close)
        book=store.import_data(FB2,'fixture.fb2')
        generation=store.create_generation(book['id'],{'pack_path':self.pack['_root'],
            'pack_fingerprint':self.pack['_fingerprint'],'device':'cpu','align_words':False,'post_processing':'on'})
        jobs=Jobs(store,self.root);self.addCleanup(jobs.shutdown)
        first,second=store.generation(generation['id'])['segments'][:2]
        first={**first,'warning':'Existing notice.'}
        store.execute('UPDATE segments SET warning=? WHERE generation_id=? AND id=?',(first['warning'],generation['id'],first['id']))
        for round_no in range(3):
            jobs._render_hold(generation['id'],first,'Automatic quality retakes exhausted; no failed audio was published.')
            jobs._render_hold(generation['id'],second,'Provisional take awaiting voice qualification.')
            self.assertEqual(round_no<2,jobs._retry_word_holds(generation['id']))
            current=store.generation(generation['id']);by_id={s['id']:s for s in current['segments']}
            self.assertEqual('review',by_id[second['id']]['status'])
            if round_no<2:
                self.assertEqual('pending',by_id[first['id']]['status'])
                self.assertEqual('Existing notice.',by_id[first['id']]['warning'])
        self.assertEqual(18,current['analysis']['takes'][first['id']])
        self.assertEqual(2,len(current['analysis']['automatic_word_hold_history']))

    def test_native_backend_refuses_active_edge_before_publishing(self):
        output=self.root/'unsafe-output.wav'
        def run(command,**kwargs):
            raw=Path(command[command.index('--out')+1])
            x=np.r_[np.zeros(1000),np.full(23000,.2)].astype('float32')
            sf.write(raw,x,24000,subtype='PCM_16')
            return types.SimpleNamespace(returncode=0,stderr='')
        payload={'pack':self.pack,'device':'cpu','resolved_speech':self.resolved,'voice_id':'n',
                 'text':'Hello.','delivery':'neutral','take':0,'output':str(output)}
        with patch.object(HiggsBackend,'probe',return_value=self.resolved),patch('rimsky.higgs_backend.run_cli',side_effect=run):
            with self.assertRaisesRegex(SpeechHoldError,'active abrupt audio edge'):HiggsBackend().render(payload)
        self.assertFalse(output.exists())

    def test_failed_automatic_quality_retakes_publish_nothing(self):
        jobs=Jobs.__new__(Jobs);jobs._check=lambda:None;jobs._intact=lambda *args:None
        wav=self.root/'failed.wav';attempts=[]
        def speak(request,output):
            attempts.append(request['take']);output.write_bytes(b'bad')
            raise SpeechHoldError('Active audio edge')
        jobs._speak=speak
        with self.assertRaisesRegex(SpeechHoldError,'no failed audio was published'):
            jobs._synthesize('g',{}, {'post_processing':'on','pack':self.pack,'take':0,'text':'Hello.'},wav)
        self.assertEqual([0,1,2],attempts)
        self.assertFalse(wav.exists())

    def test_automatic_take_survives_generation_resume_with_its_real_cache_key(self):
        from rimsky.speech_quality import transcript_check
        store=Store(self.root/'retake-library');self.addCleanup(store.close)
        store.update_settings({'cast_review':False,'device':'cpu'})
        book=store.import_data(FB2,'fixture.fb2')
        generation=store.create_generation(book['id'],{'pack_path':self.pack['_root'],
            'pack_fingerprint':self.pack['_fingerprint'],'device':'cpu','align_words':False,'post_processing':'on'})
        test=self
        class Engine(SyntheticEngine):
            def __init__(self,reject):super().__init__();self.reject=reject;self.takes=[]
            def call(self,method,payload,stop):
                if method=='narrative_frame':return protocol_frame(payload)
                if method=='probe':return dict(test.resolved)
                if method=='verify_audio':
                    heard=payload['text']
                    if self.reject:self.reject=False;heard='Wrong words.'
                    signature=digest({k:v for k,v in payload['pack']['files'].items() if k.startswith('verifier/')})
                    return transcript_check(payload['text'],heard,file_digest(Path(payload['audio'])),signature)
                if method=='render':
                    self.takes.append(payload.get('take',0));result=super().call(method,payload,stop)
                    voice=next(v for v in payload['pack']['voices'] if v['id']==payload['voice_id'])
                    plan=synthesis_plan(payload['pack'],voice,payload['text'],payload['delivery'],payload.get('controls'),payload['resolved_speech'],payload.get('take',0))
                    return {**result,'synthesis':{**plan,'plan_sha256':digest(plan)}}
                return super().call(method,payload,stop)
        def run(reject):
            engine=Engine(reject);jobs=protocol_jobs(Jobs(store,self.root,engine_factory=lambda _:engine))
            jobs.start(generation['id']);jobs.thread.join(15)
            self.assertFalse(jobs.thread.is_alive());jobs.shutdown()
            current=store.generation(generation['id']);self.assertEqual('ready',current['status'],current['error'])
            return engine,current
        first,current=run(True)
        self.assertEqual([0,1],first.takes[:2])
        self.assertTrue(current['analysis']['automatic_takes'])
        store.status(generation['id'],'failed')
        second,resumed=run(False)
        self.assertEqual(0,second.rendered)
        self.assertEqual([s['audio_hash'] for s in current['segments']],[s['audio_hash'] for s in resumed['segments']])

    def test_source_bound_word_repair_is_automatic_and_resumes_using_native_cache_keys(self):
        import re
        from rimsky.speech_quality import transcript_check
        store=Store(self.root/'word-repair-library');self.addCleanup(store.close)
        store.update_settings({'cast_review':False,'device':'cpu'})
        book=store.import_data(FB2,'fixture.fb2');before=copy.deepcopy(store.book(book['id'])['chapters'])
        generation=store.create_generation(book['id'],{'pack_path':self.pack['_root'],
            'pack_fingerprint':self.pack['_fingerprint'],'device':'cpu','align_words':False,'post_processing':'on'})
        test=self
        class Engine(SyntheticEngine):
            def call(self,method,payload,stop):
                if method=='word_recovery':
                    from rimsky.word_recovery import diagnose
                    return diagnose(Path(payload['folder']),payload['request'])
                if method=='narrative_frame':return protocol_frame(payload)
                if method=='probe':return dict(test.resolved)
                if method=='verify_audio':
                    signature=digest({k:v for k,v in payload['pack']['files'].items() if k.startswith('verifier/')})
                    heard=payload['text'] if '\u0301' in payload['text'] else 'different words'
                    if '\u0301' in payload['text'] and getattr(self,'fail_native_once',False):
                        self.fail_native_once=False;heard='different words'
                    return transcript_check(payload['text'],heard,file_digest(Path(payload['audio'])),signature)
                if method=='render':
                    result=super().call(method,payload,stop)
                    voice=next(v for v in payload['pack']['voices'] if v['id']==payload['voice_id'])
                    plan=synthesis_plan(payload['pack'],voice,payload['text'],payload['delivery'],payload.get('controls'),
                        payload['resolved_speech'],payload.get('take',0))
                    return {**result,'synthesis':{**plan,'plan_sha256':digest(plan)}}
                return super().call(method,payload,stop)
        def diagnosis(folder,request):
            from rimsky.word_recovery import SYSTEM,schema
            unit=request['units'][0];text=unit['spoken'];vowel=re.search('[аеёиоуыэюяАЕЁИОУЫЭЮЯ]',text)
            native=text[:vowel.end()]+'\u0301'+text[vowel.end():]
            answer={'answer':{'diagnoses':[{'unit_id':unit['unit_id'],'native_candidates':[native],
                'source_evidence':unit['source_quote'],'classification':'likely_synthesis',
                'reason':'A test-bound pronunciation repair','independent_check':'audio-only word check'}]}}
            write_json(folder/'request.json',{'system':SYSTEM,'request':request,
                'schema':schema([unit['unit_id']],[unit['source_quote']]),
                'provider':'claude_cli','model':'claude-opus-5-5','effort':'high'})
            answer['request_sha256']=file_digest(folder/'request.json')
            write_json(folder/'answer.json',answer);return answer
        def run(fail_native_once=False):
            engine=Engine();engine.fail_native_once=fail_native_once
            jobs=protocol_jobs(Jobs(store,self.root,engine_factory=lambda _:engine))
            with patch('rimsky.word_recovery.diagnose',side_effect=diagnosis):
                jobs.start(generation['id']);jobs.thread.join(20)
            self.assertFalse(jobs.thread.is_alive());jobs.shutdown()
            current=store.generation(generation['id']);self.assertEqual('ready',current['status'],current['error'])
            return engine,current
        first,current=run();self.assertGreater(first.rendered,0)
        self.assertTrue(list((store.root/'word-recovery-evidence').glob('*.wav')))
        repairs=[r for part in current['analysis']['automatic_takes'].values() for r in part.values() if r.get('spoken_text_plan')]
        self.assertTrue(repairs);self.assertTrue(all(r['policy']=='source_bound_word_recovery_v2' for r in repairs))
        self.assertEqual(before,store.book(book['id'])['chapters'])
        store.status(generation['id'],'failed');second,resumed=run()
        self.assertEqual(second.rendered,0)
        self.assertEqual([s['audio_hash'] for s in current['segments']],[s['audio_hash'] for s in resumed['segments']])
        # A verifier change forces another native take. Its pronunciation
        # recipe must survive an ordinary seed retake and the next restart.
        for metadata_path in (store.root/'audio').glob('*.json'):
            metadata=json.loads(metadata_path.read_text('utf8'))
            for audit in metadata.get('word_checks_parts',[metadata.get('word_check')]):
                if audit:audit['verifier_sha256']='stale-verifier'
            write_json(metadata_path,metadata)
        store.status(generation['id'],'failed');third,repaired_again=run(fail_native_once=True)
        self.assertGreater(third.rendered,0)
        records=[r for part in repaired_again['analysis']['automatic_takes'].values() for r in part.values()]
        self.assertTrue(all(r.get('spoken_text_plan') and r.get('native_text') and r.get('diagnosis_path') for r in records))
        store.status(generation['id'],'failed');fourth,final=run()
        self.assertEqual(fourth.rendered,0)
        self.assertEqual([s['audio_hash'] for s in repaired_again['segments']],[s['audio_hash'] for s in final['segments']])

    def test_decoding_recovery_keeps_pack_and_words_and_resumes_without_rendering(self):
        from rimsky.speech_quality import transcript_check
        from rimsky.recovery_decoding import execution_pack
        original=copy.deepcopy(self.pack)
        self.assertIs(self.pack,execution_pack(self.pack))
        store=Store(self.root/'decoding-library');self.addCleanup(store.close)
        store.update_settings({'cast_review':False,'device':'cpu'})
        book=store.import_data(FB2,'fixture.fb2')
        generation=store.create_generation(book['id'],{'pack_path':self.pack['_root'],
            'pack_fingerprint':self.pack['_fingerprint'],'device':'cpu','align_words':False,'post_processing':'on'})
        test=self
        class Engine(SyntheticEngine):
            def __init__(self):super().__init__();self.verified_temperature=None
            def call(self,method,payload,stop):
                if method=='narrative_frame':return protocol_frame(payload)
                if method=='probe':return dict(test.resolved)
                if method=='word_recovery':raise AssertionError('A successful decoding recovery should not ask Opus')
                if method=='verify_audio':
                    signature=digest({k:v for k,v in payload['pack']['files'].items() if k.startswith('verifier/')})
                    heard=payload['text'] if self.verified_temperature==.55 else 'Wrong words'
                    return transcript_check(payload['text'],heard,file_digest(Path(payload['audio'])),signature)
                if method=='render':
                    result=super().call(method,payload,stop)
                    self.verified_temperature=payload['pack']['speech']['sampling']['temperature']
                    voice=next(v for v in payload['pack']['voices'] if v['id']==payload['voice_id'])
                    plan=synthesis_plan(payload['pack'],voice,payload['text'],payload['delivery'],payload.get('controls'),payload['resolved_speech'],payload.get('take',0))
                    return {**result,'synthesis':{**plan,'plan_sha256':digest(plan)}}
                return super().call(method,payload,stop)
        def run():
            engine=Engine();jobs=protocol_jobs(Jobs(store,self.root,engine_factory=lambda _:engine))
            jobs.start(generation['id']);jobs.thread.join(20);self.assertFalse(jobs.thread.is_alive());jobs.shutdown()
            current=store.generation(generation['id']);self.assertEqual('ready',current['status'],current['error'])
            return engine,current
        first,current=run();self.assertGreater(first.rendered,0)
        records=[r for parts in current['analysis']['automatic_takes'].values() for r in parts.values()]
        self.assertTrue(records);self.assertTrue(all(r.get('decoding_profile')=='careful_full' for r in records))
        store.status(generation['id'],'failed');second,resumed=run();self.assertEqual(0,second.rendered)
        self.assertEqual([s['audio_hash'] for s in current['segments']],[s['audio_hash'] for s in resumed['segments']])
        self.assertEqual(original,self.pack)

    def test_production_frame_pass_precedes_casting_and_is_checkpointed(self):
        from rimsky.speech_quality import transcript_check
        pack=fixture(self.root/'production-pack',purpose='production')
        store=Store(self.root/'frame-library');self.addCleanup(store.close)
        store.update_settings({'cast_review':False,'device':'cpu'})
        book=store.import_data(FB2,'fixture.fb2')
        generation=store.create_generation(book['id'],{'pack_path':pack['_root'],
            'pack_fingerprint':pack['_fingerprint'],'device':'cpu','align_words':False,'post_processing':'on'})
        test=self
        class Engine(SyntheticEngine):
            def __init__(self,opening=False):super().__init__();self.events=[];self.opening=opening
            def call(self,method,payload,stop):
                self.events.append(method)
                if method=='performance_direction':
                    body=payload['request']
                    if payload['kind']=='name_stress':return super().call(method,payload,stop)
                    if payload['kind']=='profile':
                        source=body['sources'][0]
                        return {'profiles':[{'character_id':c['id'],'nature':'human','pitch':'ordinary',
                            'sound_role':'human',
                            'baseline_register':'ordinary','baseline_technique':'ordinary','aliases':[],
                            'evidence':{'source_id':source['id'],'quote':source['text']},
                            'needs_adjudication':False} for c in body['characters']]}
                    return {'turns':[{'turn_id':t['turn_id'],'register':'ordinary','technique':'ordinary',
                        'event_kind':'none',
                        'audible_stance':'ordinary fixture narration','addressee':'','warmth':'none',
                        'evidence':t['text'],'needs_adjudication':False,'audio_kind':'speech'} for t in body['turns']]}
                if method=='narrative_frame':
                    targets=payload['request']['targets']
                    evidence=next((t for t in targets if t['kind'] not in ('heading','separator')),targets[0])
                    return {'ranges':[{'first':targets[0]['id'],'last':targets[-1]['id'],
                        'role':'opening_vignette' if self.opening else 'main_story',
                        'register':'opening' if self.opening else 'ordinary','perspective':'third_person',
                        'focal_identity':'fixture','relation_to_main':'Main story','confidence':'high','reason':'Fixture narrative.',
                        'evidence':[{'id':evidence['id'],'quote':evidence['text']}]}]}
                if method=='probe':return dict(test.resolved)
                if method=='verify_audio':
                    signature=digest({k:v for k,v in payload['pack']['files'].items() if k.startswith('verifier/')})
                    return transcript_check(payload['text'],payload['text'],file_digest(Path(payload['audio'])),signature)
                if method=='render':
                    result=super().call(method,payload,stop)
                    voice=next(v for v in pack['voices'] if v['id']==payload['voice_id'])
                    plan=synthesis_plan(pack,voice,payload['text'],payload['delivery'],payload.get('controls'),payload['resolved_speech'],payload.get('take',0))
                    return {**result,'synthesis':{**plan,'plan_sha256':digest(plan)}}
                return super().call(method,payload,stop)
        def run():
            engine=Engine();jobs=Jobs(store,self.root,engine_factory=lambda _:engine)
            jobs.start(generation['id']);jobs.thread.join(15)
            self.assertFalse(jobs.thread.is_alive());jobs.shutdown()
            current=store.generation(generation['id']);self.assertEqual('held',current['status'],current['error'])
            return engine,current
        engine,current=run()
        self.assertTrue(current['analysis']['narrative_frames']['by_source'])
        self.assertTrue(current['analysis']['scene_direction'])
        self.assertFalse(any(g['passed'] for g in current['analysis']['chapter_acceptance'].values()))
        self.assertFalse(any(s['status']=='ready' for s in current['segments']))
        self.assertGreater(engine.events.index('analyze'),max(i for i,m in enumerate(engine.events) if m=='narrative_frame'))
        store.status(generation['id'],'failed')
        resumed,_=run();self.assertNotIn('narrative_frame',resumed.events)
        # A special frame is not silently routed to ordinary narration when its
        # qualified register is missing. This exercises real source-ID mapping.
        opening_book=store.import_data(FB2.replace(b'<book-title>',b'<book-title>Opening fixture '),'opening.fb2')
        new=store.create_generation(opening_book['id'],{'pack_path':pack['_root'],
            'pack_fingerprint':pack['_fingerprint'],'device':'cpu','align_words':False,'post_processing':'on'})
        missing=Engine(opening=True);jobs=Jobs(store,self.root,engine_factory=lambda _:missing)
        jobs.start(new['id']);jobs.thread.join(15);self.assertFalse(jobs.thread.is_alive());jobs.shutdown()
        stopped=store.generation(new['id'])
        self.assertEqual('held',stopped['status'],stopped['error'])
        self.assertTrue(any('no single qualified voice' in s['warning'] for s in stopped['segments']))
        self.assertNotIn('render',missing.events)


if __name__ == '__main__':
    unittest.main()
