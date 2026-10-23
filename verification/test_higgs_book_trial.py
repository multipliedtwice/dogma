import json
import sys
import tempfile
import unittest
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from prepare_higgs_book_trial import chunks
from render_higgs_book_trial import spoken_text, sequence_request
from run_higgs_book_mvp import sentence_requests

class HiggsBookTrialTests(unittest.TestCase):
    def test_structure_keeps_dash_and_parenthetical_offsets(self):
        text='— Да, — сказал он (не громко), — пойдём. Потом вернёмся.'
        result=chunks(text)
        self.assertEqual(''.join(c['text'] for c in result),text)
        self.assertTrue(any(c['text'].startswith('(не громко)') for c in result))
        for c in result:self.assertEqual(text[c['start']:c['end']],c['text'])
        self.assertGreaterEqual(sum('—' in c['text'] for c in result),3)

    @unittest.skipUnless(importlib.util.find_spec('num2words'),'Optional listening-trial number conversion dependency')
    def test_spoken_numbers_preserve_meaning_and_separate_brackets(self):
        self.assertEqual(spoken_text('Получено: 15 000 золотых'),'Получено: пятнадцать тысяч золотых')
        self.assertEqual(spoken_text('Зачислено: 11\u202f400 золотых'),'Зачислено: одиннадцать тысяч четыреста золотых')
        self.assertEqual(spoken_text('1 2 3'),'один два три')
        self.assertEqual(spoken_text('15 000','legacy-v1'),'пятнадцать ноль')
        self.assertEqual(spoken_text('Глава 1'),'Глава первая')
        self.assertEqual(spoken_text('Человек 162-го уровня'),'Человек сто шестьдесят второго уровня')
        self.assertEqual(spoken_text('Мудрость 29(свободных ячеек — 2)'),
                         'Мудрость двадцать девять, свободных ячеек — два')
        self.assertEqual(spoken_text('<Извлечь> — <Подтвердить>'),'Извлечь — Подтвердить')
        self.assertEqual(spoken_text('— Я видел ее. Всё ясно.'),'Я видел её. Всё ясно.')
        with self.assertRaises(ValueError):spoken_text('Статус {unknown}')

    def test_batch_keeps_exact_reference_controls_and_seed(self):
        plan={'prompt':'<|emotion:enthusiasm|>Пойдём!',
              'reference':{'audio':'ref.wav','text':'Проверенный образец.'},
              'sampling':{'seed':71,'temperature':.8,'top_k':50,'top_p':.95,'repetition_penalty':1.1,'do_sample':True},
              'max_tokens':2048,'chunk_mode':'tag_aware','chunk_size':65536}
        request=sequence_request('u0001',plan)
        self.assertEqual(request['text'],plan['prompt'])
        self.assertEqual(request['seed'],71)
        self.assertEqual(request['reference_text'],plan['reference']['text'])
        self.assertEqual(Path(request['voice_ref']),ROOT/'ref.wav')
        self.assertEqual(request['text_chunk_size'],65536)
        self.assertEqual(request['max_tokens'],2048)

    def test_onset_retry_changes_prompt_key_but_preserves_words_and_voice(self):
        from render_higgs_book_trial import onset_pause_plan
        from rimsky.util import digest
        original={'text':'Еще и Стасу заплатили двадцать процентов,',
                  'prompt':'<|prosody:expressive_low|>Еще и Стасу заплатили двадцать процентов,',
                  'reference':{'audio':'ref.wav'},'sampling':{'seed':7}}
        paused=onset_pause_plan(original)
        self.assertEqual(paused['text'],original['text'])
        self.assertEqual(paused['reference'],original['reference'])
        self.assertEqual(paused['sampling'],original['sampling'])
        self.assertNotEqual(digest(paused),digest(original))
        self.assertIn('<|prosody:pause|>',paused['prompt'])
        self.assertNotIn('<|prosody:pause|>',original['prompt'])
        self.assertFalse(paused['onset_pause']['listening_approved'])

    @unittest.skipUnless(importlib.util.find_spec('num2words'),'Optional listening-trial number conversion dependency')
    def test_untrusted_recognizer_ordinal_does_not_abort_other_checks(self):
        from normalize_higgs_word_audit import normalize
        from rimsky.util import write_json,file_digest
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)
            write_json(p/'render-plan.json',{'units':[
                {'id':'bad','spoken':'семьдесят третье чудо'},
                {'id':'good','spoken':'Привет.'}]})
            write_json(p/'word-checks-1.7B.json',{
                'plan_sha256':file_digest(p/'render-plan.json'),'verifier':{},'checks':{
                    key:{'heard':heard,'audio_sha256':'a'*64,'audio':key+'.wav','synthesis_key':key}
                    for key,heard in [('bad','73е чудо'),('good','Привет.')]}})
            checks=normalize(p)['checks']
            self.assertEqual(checks['bad']['status'],'asr_spoken_text_unresolved')
            self.assertEqual(checks['bad']['heard_raw'],'73е чудо')
            self.assertEqual(checks['good']['status'],'asr_exact_match')

    @unittest.skipUnless(importlib.util.find_spec('num2words'),'Optional listening-trial number conversion dependency')
    def test_cached_word_check_requires_exact_take_and_verifier_bindings(self):
        from unittest.mock import patch
        from regenerate_higgs_book_trial import cached_audit
        from rimsky.util import write_json,file_digest,digest
        from rimsky.speech_quality import transcript_check
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);(p/'u.wav').write_bytes(b'immutable audio')
            write_json(p/'render-plan.json',{'units':[{'id':'u','key':'k','spoken':'Привет.'}]})
            binding=file_digest(p/'render-plan.json');audio_sha=file_digest(p/'u.wav');verifier={'version':1,'device':'cuda:0'}
            write_json(p/'receipts.json',{'plan_sha256':binding,'takes':{
                'u':{'key':'k','audio':'u.wav','audio_sha256':audio_sha}}})
            row={**transcript_check('Привет.','Привет.',audio_sha,digest(verifier)),
                 'audio':'u.wav','synthesis_key':'k'}
            report={'plan_sha256':binding,'verifier':verifier,'checks':{'u':row}}
            write_json(p/'word-checks-1.7B.json',report)
            with patch('regenerate_higgs_book_trial.audit_configuration',return_value=verifier):
                self.assertEqual(cached_audit(p)['checks']['u']['status'],'asr_exact_match')
                row['synthesis_key']='another take'
                write_json(p/'word-checks-1.7B.json',report)
                with self.assertRaises(ValueError):cached_audit(p)
                row['synthesis_key']='k';write_json(p/'word-checks-1.7B.json',report)
                (p/'u.wav').write_bytes(b'changed audio')
                with self.assertRaises(ValueError):cached_audit(p)

    @unittest.skipUnless(sys.platform=='win32','Windows shared audio-resource guard')
    def test_audio_resource_guard_waits_without_stopping_music(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from regenerate_higgs_book_trial import wait_for_audio_resources
        replies=[SimpleNamespace(returncode=0,stdout='busy'),SimpleNamespace(returncode=0,stdout='free')]
        with patch('regenerate_higgs_book_trial.subprocess.run',side_effect=replies) as probe, \
             patch('regenerate_higgs_book_trial.time.sleep') as sleep:
            wait_for_audio_resources()
        self.assertEqual(probe.call_count,2)
        sleep.assert_called_once()
        self.assertNotIn('Stop-Process',probe.call_args.args[0][-1])

    def test_explanatory_dash_joins_but_author_switch_stays_split(self):
        text='Ты — ветеран. Да, — сказал он, — пойдём.'
        parts=[(0,3,'speech','terek'),(3,14,'speech','terek'),
               (14,18,'speech','terek'),(18,31,'narrator','narrator'),(31,len(text),'speech','terek')]
        units=[{'id':f'u{i}','source_id':'p','text':text[a:b],'start':a,'end':b,'mode':mode,
                'speaker':speaker,'voice':speaker,'mood':'baseline'} for i,(a,b,mode,speaker) in enumerate(parts)]
        result=sentence_requests(units,[{'id':'p','text':text}])
        self.assertEqual(result[0]['text'],'Ты — ветеран. ')
        self.assertEqual(len(result[0]['analysis_parts']),2)
        self.assertEqual(''.join(r['text'] for r in result),text)
        self.assertEqual([r['speaker'] for r in result],['terek','terek','narrator','terek'])
        self.assertEqual(result[0]['render_seed'],20261013)
        self.assertEqual(result[-1]['render_seed'],20261017)

    def test_atomic_reports_survive_concurrent_readers(self):
        import threading
        from rimsky.util import write_json
        from higgs_trial_io import read_json
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'report.json'
            write_json(target,{'step':0,'payload':[0]*3000})
            done=threading.Event();errors=[]
            def reader():
                while not done.is_set():
                    try:
                        value=read_json(target)
                        if value['payload']!=[value['step']]*3000:errors.append('partial snapshot')
                    except Exception as exc:errors.append(str(exc));break
            thread=threading.Thread(target=reader);thread.start()
            try:
                for step in range(1,20):write_json(target,{'step':step,'payload':[step]*3000})
            finally:done.set();thread.join()
            self.assertEqual(errors,[])
            self.assertEqual(read_json(target)['step'],19)

    def test_resume_does_not_publish_a_leftover_uncommitted_wav(self):
        import copy
        import numpy as np
        import soundfile as sf
        from unittest.mock import patch
        import render_higgs_book_trial as renderer
        from rimsky.util import file_digest
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);out=root/'out';out.mkdir()
            assignment=root/'assignment.json'
            assignment.write_text(json.dumps({'units':[{'id':'u1','chapter_id':'c1','source_id':'p',
                'text':'Слово.','speaker':'speaker','voice':'voice','mood':'baseline','kind':'paragraph'}]}),encoding='utf-8')
            sf.write(out/'u1.wav',np.full(24000,.02),24000,subtype='PCM_16')
            old_hash=file_digest(out/'u1.wav')
            pack={'speech':{'model':'model'},'files':{'model':'a'*64},'voices':[{'id':'voice'}]}
            synthesis={'text':'Слово.','prompt':'Слово.','voice_family':'voice','reference':{'audio':'ref.wav','text':'Образец.','sha256':'b'*64},
                'sampling':{'seed':1,'temperature':.8,'top_k':50,'top_p':.95,'repetition_penalty':1.1,'do_sample':True},
                'max_tokens':2048,'chunk_mode':'tag_aware','chunk_size':65536}
            captured=[]
            class FakeProcess:
                returncode=0
                def __init__(self,command,**kwargs):
                    staging=Path(command[command.index('--out-dir')+1]);captured.append(staging)
                    sf.write(staging/'u1.wav',np.full(24000,.06),24000,subtype='PCM_16')
                def poll(self):return 0
            with patch.object(renderer,'make_packs',return_value=({'voice':pack},{})), \
                 patch.object(renderer,'spoken_text',side_effect=lambda text,*args:text), \
                 patch.object(renderer,'synthesis_plan',side_effect=lambda *a,**k:copy.deepcopy(synthesis)), \
                 patch.object(renderer.subprocess,'Popen',FakeProcess), \
                 patch.object(renderer,'assemble') as publication:
                renderer.render(assignment,out,['c1'])
                publication.assert_not_called()
            receipt=json.loads((out/'receipts.json').read_text('utf-8'))['takes']['u1']
            self.assertNotEqual(captured[0],out)
            self.assertNotEqual(receipt['audio_sha256'],old_hash)
            self.assertEqual(receipt['audio_sha256'],file_digest(out/'u1.wav'))
            self.assertEqual((out/'u1.wav').read_bytes(),(captured[0]/'u1.wav').read_bytes())

if __name__=='__main__':unittest.main()
