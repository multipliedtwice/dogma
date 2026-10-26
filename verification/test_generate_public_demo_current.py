"""Synthetic current-recipe/technical-resume checks; no providers, GPU or ASR."""
import copy,json,sys,tempfile,unittest,wave
from pathlib import Path
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import generate_public_demo_current as m
from rimsky import listener_stress


class CurrentDemo(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.folder=self.root/'source';self.output=self.root/'current-book';self.profile=self.root/'human.sqlite3'
        original=m.feedback.source_store
        self.scope=patch.object(m.feedback,'source_store',side_effect=lambda root:original(root,self.profile))
        self.scope.start();self.addCleanup(self.scope.stop)

    def source(self,language='English'):
        texts=['Замок.','Привет.'] if language=='Russian' else ['A title.','Hello.']
        book={'id':'a'*32,'source_hash':'a'*64,'language':'ru' if language=='Russian' else 'en',
            'title':'Fixture','author':'Author','chapters':[{'id':'c0001','blocks':[
                {'id':f'p{i}','kind':'heading' if i==0 else 'paragraph','text':t} for i,t in enumerate(texts)]}]}
        m.write(self.folder/'prepared/book.json',book)
        units=[{'id':f'u{i:04d}','chapter_id':'c0001','source_id':f'p{i}','kind':'heading' if i==0 else 'paragraph',
            'start':0,'end':len(t),'text':t,'mode':'narrator','voice':'narrator_baseline','mood':'calm',
            'palette_reference':{'path':str(self.root/'private-reference.wav'),'audio_sha256':'b'*64,'text':'PRIVATE REFERENCE',
                'reviewed':True,'voice_family':'narrator_baseline','mood':'calm'}} for i,t in enumerate(texts)]
        assigned={'language':language,'title':book['title'],'author':book['author'],'source_sha256':m.file_digest(self.folder/'prepared/book.json'),
            'source_epub_sha256':book['source_hash'],'cast':{},'units':units}
        m.write(self.folder/'analysis/assigned.json',assigned)
        rp=self.folder/'analysis/structure.request.json';m.write(rp,{'fixture':'exact source'})
        m.write(self.folder/'analysis/structure.answer.json',{'request_sha256':m.file_digest(rp),
            'answer':{f'p{i}':[{'text':t,'mode':'narrator'}] for i,t in enumerate(texts)}})
        return book,units

    def prepared_batch(self,data):
        entries={}
        for unit in data['units']:
            text=unit['text'].replace('Замок','За́мок')
            row={'policy':m.recipe.POLICY,'book_sha256':data['book_sha256'],'source':m.recipe.source_binding(unit['source']),
                'text_sha256':m.digest(unit['text'].encode('utf8')),'automatic_text':text,'module_pins':m.recipe.module_pins(),
                'model_pins':{'fixture':'no model loaded'}}
            row['sound_sha256']=m.digest(m.recipe.sound_binding(row));row['sha256']=m.digest(row)
            entries[m.recipe.entry_key(unit['text'],unit['source'])]=row
        return {'prepared_units':entries,'request_sha256':m.digest(data),'sha256':m.digest(entries)}

    def note(self,book,unit,marked,identifier='human',created='2026-10-26T00:00:00Z'):
        with m.feedback.source_store(self.folder/'listener-state') as store:
            listener_stress.record(store,book,{'id':identifier,'book_id':book['id'],'generation_id':'fixture',
                'segment_id':unit['id'],'chapter_id':unit['chapter_id'],'source_id':unit['source_id'],'kind':'stress',
                'selected_text':'Замок','intended_stress':marked,'passage_text':unit['text'],'text_offset':0,
                'created':created,'comment':'Explicit fixture instruction'},_project=False)

    def test_english_never_dispatches_russian_preparation_and_preserves_source_kinds(self):
        book,units=self.source()
        with patch.object(m.recipe,'prepare',side_effect=AssertionError('Russian processing forbidden')):
            assigned=m.prepare(self.folder);m.validate_preparation(self.folder)
        self.assertEqual([u['text'] for u in assigned['units']],[u['text'] for u in units])
        self.assertEqual([u['kind'] for u in assigned['units']],['heading','paragraph'])
        self.assertTrue(all(u['synthesis_text']==u['spoken'] for u in assigned['units']))
        (self.folder/'assignment.json').unlink() # Interrupted assignment/result commit from an earlier writer.
        with patch.object(m.recipe,'prepare',side_effect=AssertionError('Must recover, not call providers')):
            self.assertEqual(assigned,m.prepare(self.folder))

    def test_current_runtime_heading_binding_then_human_last_and_new_note_invalidates(self):
        book,units=self.source('Russian');self.note(book,units[0],'Замо́к')
        lexical={'names':{},'common':[],'proper_nouns':{}}
        with patch.object(m,'names',return_value=lexical),patch.object(m.recipe,'prepare',side_effect=self.prepared_batch) as call:
            assigned=m.prepare(self.folder)
        self.assertEqual([u['kind'] for u in call.call_args.args[0]['units']],['heading','paragraph'])
        self.assertEqual(call.call_args.args[0]['recipe'],m.recipe.config('codex_cli'))
        self.assertEqual(assigned['units'][0]['synthesis_text'],'Замо́к.')
        self.assertEqual(assigned['units'][0]['text'],'Замок.')
        self.assertIn('heading_stress',assigned['units'][0]['pronunciation']['prepared_entry']['module_pins'])
        self.note(book,units[0],'За́мок','newer','2026-10-26T01:00:00Z')
        with self.assertRaisesRegex(ValueError,'New human instruction'):m.validate_preparation(self.folder)

    def test_freeze_resume_after_manifest_before_frozen_commit_uses_same_owned_manifest(self):
        self.source();m.prepare(self.folder)
        base={'sampling':{'top_k':50},'freeze_implementation_sha256':'f'*64};original=m.write;failed=[]
        def interrupted(path,value,**kwargs):
            if Path(path).name=='frozen.json' and not failed:
                failed.append(True);raise OSError('Synthetic interrupted paired metadata commit')
            return original(path,value,**kwargs)
        with patch.object(m.legacy,'freeze',return_value=copy.deepcopy(base)) as builder,patch.object(m,'write',side_effect=interrupted):
            with self.assertRaises(OSError):m.freeze(self.folder,None)
        self.assertFalse((self.folder/'frozen.json').exists());saved=m.read(self.folder/'manifest.json')
        self.assertEqual(saved['sampling'],m.SAMPLING);self.assertEqual(builder.call_args.kwargs,{'persist':False})
        with patch.object(m.legacy,'freeze',return_value=copy.deepcopy(base)):
            self.assertEqual(saved,m.freeze(self.folder,None))
        self.assertEqual(m.read(self.folder/'frozen.json')['manifest'],saved)

    def native_config(self):
        unit={'id':'u1','text':'Hello.','spoken':'Hello.','synthesis_text':'Hello.','source_id':'p','chapter_id':'c',
            'palette_reference':{'path':str(self.root/'reference.wav'),'audio_sha256':'b'*64,'text':'PRIVATE','reviewed':True}}
        config={'source_generation':'a'*64,'frozen':{'assignment':{'units':[unit],'language':'English'},
            'manifest':{'speech_model':{'path':'model','sha256':'m'},'runner':{'path':'runner','sha256':'r'},'sampling':m.SAMPLING,'seed':42}}}
        return config,unit

    def base_request(self,config,unit,index):
        return {'source':unit,'request':{'id':'u1','text':unit['synthesis_text'],'seed':42,'max_tokens':128,**m.SAMPLING}}

    def test_cap_extension_then_seed_only_technical_budget(self):
        config,unit=self.native_config()
        with patch.object(m.native,'request_spec',side_effect=self.base_request):
            requests=[m.request(config,unit,0,n) for n in range(3)]
        self.assertEqual([r['request']['max_tokens'] for r in requests],[128,384,384])
        self.assertEqual([r['request']['seed'] for r in requests],[42,42,43])
        for row in requests:self.assertEqual(row['request']['text'],'Hello.');self.assertEqual(row['take'],0)
        with self.assertRaises(ValueError):m.request(config,unit,0,3)

    def test_cap_ceiling_has_only_one_distinct_seed_retry_even_for_noncap_failure(self):
        config,unit=self.native_config()
        def ceiling(*args):
            result=self.base_request(*args);result['request']['max_tokens']=2048;return result
        with patch.object(m.native,'request_spec',side_effect=ceiling):
            first=m.request(config,unit,0,0);second=m.request(config,unit,0,1)
            self.assertEqual((first['request']['max_tokens'],second['request']['max_tokens']),(2048,2048))
            self.assertEqual((first['request']['seed'],second['request']['seed']),(42,43))
            with self.assertRaisesRegex(ValueError,'ceiling'):m.request(config,unit,0,2)
            row={'history':[{'status':'technical_incomplete','native_cap_reported':False}]*2,'selected':None}
            self.assertFalse(m.retryable(row,config,0))

    def session_fixture(self,completed):
        config,unit=self.native_config();job=self.root/'job'
        with patch.object(m.native,'request_spec',side_effect=self.base_request):spec=m.request(config,unit,0,0)
        session=job/'sessions'/m.digest([spec]);sequence=session/'request.json'
        m.write(sequence,{'requests':[spec['request']]})
        m.write(session/'physical-start.json',{'specs':[spec],'sequence_sha256':m.file_digest(sequence),'command':m.native_command(config,session)})
        m.write(session/'physical-result.json',{'returncode':0 if completed else 1,'error':None})
        raw=session/'native'/(spec['request']['id']+'.wav');raw.parent.mkdir()
        with wave.open(str(raw),'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(24000);stream.writeframes(bytes(4800))
        (session/'native.log').write_text(f"audio_out={raw}\nmetrics[{spec['request']['id']}].audio_duration_ms=100\n" if completed
            else 'max_tokens(128) before EOC; incomplete output',encoding='utf8')
        return config,unit,job,session

    def test_format_valid_incomplete_wav_excluded_and_completed_take_never_retried(self):
        config,unit,job,session=self.session_fixture(False);m.collect(job,session)
        with patch.object(m.native,'request_spec',side_effect=self.base_request):
            row=m.selected(job,config)[0]
            self.assertIsNone(row['selected']);self.assertTrue(row['history'][0]['native_cap_reported']);self.assertTrue(m.retryable(row,config,0))
        # A new synthetic workspace represents a genuinely completed native output.
        self.root=self.root/'completed';self.root.mkdir()
        config,unit,job,session=self.session_fixture(True);m.collect(job,session)
        with patch.object(m.native,'request_spec',side_effect=self.base_request):
            row=m.selected(job,config)[0];self.assertIsNotNone(row['selected']);self.assertFalse(m.retryable(row,config,0))
            tampered=copy.deepcopy(row['history'][0]);tampered.update(attempt=1,spec=m.request(config,unit,0,1))
            tampered['spec_sha256']=m.digest(tampered['spec']);m.write(job/'attempts/u1/1.json',tampered)
            with self.assertRaisesRegex(ValueError,'completed take was retried'):m.selected(job,config)

    def test_failed_first_request_does_not_spend_unstarted_suffix_attempts(self):
        config,unit,job,session=self.session_fixture(False)
        suffix={**unit,'id':'u2'};config['frozen']['assignment']['units'].append(suffix)
        with patch.object(m.native,'request_spec',side_effect=self.base_request):
            for attempt in range(3):
                first=m.request(config,unit,0,attempt);later=m.request(config,suffix,1,0)
                base=job/'sessions'/f'failure-{attempt}';sequence=base/'request.json'
                m.write(sequence,{'requests':[first['request'],later['request']]})
                m.write(base/'physical-start.json',{'specs':[first,later],'command':m.native_command(config,base),
                    'sequence_sha256':m.file_digest(sequence)})
                m.write(base/'physical-result.json',{'returncode':1,'error':None})
                (base/'native.log').write_text('mode=offline\naudiocpp_cli failed: Higgs max_tokens before EOC',encoding='utf8')
                m.collect(job,base)
                self.assertEqual(m.read(base/'undispatched.json')['units'][0]['unit'],'u2')
            rows=m.selected(job,config)
        self.assertEqual(len(rows[0]['history']),3);self.assertEqual(len(rows[1]['history']),0)
        self.assertFalse(m.retryable(rows[0],config,0));self.assertTrue(m.retryable(rows[1],config,1))

    def test_backend_offline_line_alone_is_unknown_and_does_not_spend_or_repeat_a_unit(self):
        config,unit,job,session=self.session_fixture(False)
        next((session/'native').glob('*.wav')).unlink()
        (session/'native.log').write_text('family=higgs_audio_tts\ntask=tts\nmode=offline\n',encoding='utf8')
        with self.assertRaisesRegex(ValueError,'Unknown request dispatch'):m.collect(job,session)
        self.assertFalse((job/'attempts/u1/0.json').exists())
        self.assertTrue(m.read(session/'undispatched.json')['uncertain_dispatch'])
        with self.assertRaisesRegex(ValueError,'Unknown request dispatch'):m.collect(job,session)

    def test_uncertain_dispatch_persists_stopped_held_state_after_native_exit(self):
        config,unit=self.native_config();job=self.root/'job'
        child=Mock(pid=7,returncode=1);child.poll.return_value=1
        process=Mock();process.create_time.return_value=100
        def launch(command,**kwargs):
            kwargs['stdout'].write(b'family=higgs_audio_tts\ntask=tts\nmode=offline\n');kwargs['stdout'].flush()
            return child
        with patch.object(m.native,'request_spec',side_effect=self.base_request),patch.object(m,'validate_preparation'),\
             patch.object(m.native.assets,'wait_for_audio_resources'),\
             patch.object(m.native,'model_guard',return_value=(Mock(path=Path('model')),Path('runner'))),\
             patch.object(m.subprocess,'Popen',side_effect=launch),\
             patch.object(m.psutil,'Process',side_effect=[process,m.psutil.NoSuchProcess(7)]):
            spec=m.request(config,unit,0,0)
            with self.assertRaisesRegex(ValueError,'Unknown request dispatch'):m.session_run(self.folder,self.output,job,config,[spec],60)
        progress=m.read(self.output/'progress.json')
        self.assertFalse(progress['worker_running']);self.assertEqual(progress['stage'],'held')
        self.assertIn('Unknown request dispatch',progress['error'])
        self.assertFalse((job/'attempts/u1/0.json').exists())

    def test_stable_light_publication_keeps_one_mix_and_no_private_reference_fields(self):
        import numpy as np
        import soundfile as sf
        config,unit,job,session=self.session_fixture(True);m.collect(job,session)
        assignment=config['frozen']['assignment'];assignment.update(title='Public fixture',author='Author',source_epub_sha256='s'*64)
        manifest=config['frozen']['manifest'];manifest.update(music={'bed':{},'ending':{}},opening={'sha256':'o'*64},ffmpeg='fixture-ffmpeg')
        config['player_sha256']=m.file_digest(m.native.assets.PLAYER_SOURCE)
        intro=self.root/'intro.wav';music=self.root/'music.wav'
        sf.write(intro,np.zeros((8*24000,2),dtype=np.float32),24000,subtype='PCM_16')
        sf.write(music,np.zeros((24000,2),dtype=np.float32),24000,subtype='PCM_16')
        m.write(self.output/'current-job.json',{'job':job.name,'config_sha256':m.digest(config)})
        def ffmpeg(command,**kwargs):
            if '-y' in command:Path(command[-1]).write_bytes(b'synthetic encoded placeholder')
            return Mock(returncode=0)
        with patch.object(m.native,'request_spec',side_effect=self.base_request),\
             patch.object(m.native.assets,'opening',return_value=intro),patch.object(m.native.assets,'music_asset',return_value=music),\
             patch.object(m.subprocess,'run',side_effect=ffmpeg):
            status=m.publish(self.output,job,config);m.publish(self.output,job,config)
        public=self.output/'public'
        self.assertEqual([p.name for p in public.glob('*.mp3')],['current.mp3'])
        self.assertEqual([p.name for p in public.glob('*.wav')],['current.wav'])
        page=(public/'index.html').read_text('utf8');self.assertIn('color-scheme:light',page)
        self.assertNotIn('PRIVATE',page);self.assertNotIn('palette_reference',json.dumps(status))
        self.assertEqual(status['recognition_status'],'not_checked');self.assertEqual(status['physical_native_attempts'],1)

    def test_unknown_started_worker_is_not_collected_as_stopped(self):
        session=self.root/'unknown';session.mkdir();m.write(session/'physical-start.json',{})
        with self.assertRaisesRegex(ValueError,'Unknown physical worker'):m.terminal_session(session)
        self.assertFalse((session/'physical-result.json').exists())

    def test_pid_reuse_does_not_adopt_unrelated_process(self):
        identity={'pid':7,'create_time':100,'command':['native','--request-sequence','exact']}
        process=Mock();process.create_time.return_value=101;process.cmdline.return_value=identity['command']
        with patch.object(m.psutil,'Process',return_value=process):self.assertFalse(m.live(identity))
        process.create_time.return_value=100
        with patch.object(m.psutil,'Process',return_value=process):self.assertTrue(m.live(identity))

    def test_model_guard_precedes_any_physical_start_or_process(self):
        config,unit=self.native_config()
        with patch.object(m,'validate_preparation'),patch.object(m.native.assets,'wait_for_audio_resources'),\
             patch.object(m.native,'model_guard',side_effect=ValueError('model mismatch')),patch.object(m.subprocess,'Popen') as popen:
            with self.assertRaisesRegex(ValueError,'model mismatch'):m.session_run(self.folder,self.output,self.root/'job',config,[],60)
            popen.assert_not_called()
        self.assertFalse((self.root/'job/sessions').exists())

    def test_completed_resume_does_not_dispatch_another_native_or_audit(self):
        config,unit=self.native_config();job=self.root/'job'
        rows=[{'unit':'u1','history':[{'status':'rendered'}],'selected':{'status':'rendered'}}]
        with patch.object(m,'configure',return_value=(config,job)),patch.object(m,'selected',return_value=rows),\
             patch.object(m,'publish'),patch.object(m,'session_run',side_effect=AssertionError('No new synthesis')) as call:
            status=m.render(self.folder,self.output,16,60)
        call.assert_not_called();self.assertTrue(status['render_complete']);self.assertEqual(status['recognition_status'],'not_checked')
        self.assertEqual(status['post_processing'],'off');self.assertFalse(status['machine_verified'])


if __name__=='__main__':unittest.main()
