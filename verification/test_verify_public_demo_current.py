"""Independent public mix checks: temporary source/PCM only, no models/providers."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import generate_public_demo_current as producer
import verify_public_demo_current as verify
import github_pages as pages

FFMPEG=Path('C:/ffmpeg/bin/ffmpeg.exe')


class SourceCoverage(unittest.TestCase):
    def fixture(self):
        return {'chapters':[{'blocks':[{'id':'p','text':'Hello, world!'}]}]}, {'units':[
            {'id':'u0000','source_id':'p','start':0,'end':6,'text':'Hello,'},
            {'id':'u0001','source_id':'p','start':7,'end':13,'text':'world!'}]}

    def test_only_whitespace_source_gaps_are_allowed(self):
        book,assigned=self.fixture();self.assertEqual(verify.strict_coverage(book,assigned)['units'],2)
        assigned['units'][0].update(end=5,text='Hello')
        with self.assertRaisesRegex(ValueError,'non-whitespace'):verify.strict_coverage(book,assigned)

    def test_source_tail_overlap_and_reordering_are_not_ignored(self):
        for mode in('tail','overlap','order'):
            book,assigned=self.fixture()
            if mode=='tail':assigned['units'].pop()
            elif mode=='overlap':assigned['units'][1].update(start=5,text=', world!')
            else:assigned['units'].reverse()
            with self.subTest(mode=mode),self.assertRaises(ValueError):verify.strict_coverage(book,assigned)

    def test_caption_float_accumulation_is_allowed_but_sample_shift_is_not(self):
        expected=[{'id':'u0000','text':'Hello.','start':8.2,'end':9.1}]
        actual=copy.deepcopy(expected);actual[0]['start']+=1e-12
        self.assertTrue(verify.captions_match(actual,expected))
        actual[0]['start']+=1/verify.RATE
        self.assertFalse(verify.captions_match(actual,expected))


@unittest.skipUnless(FFMPEG.is_file(),'CPU ffmpeg is required for real encode/decode fixtures')
class CurrentDelivery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.source=self.root/'source';self.output=self.root/'output';rate=24000
        texts=['Hello, friend.','A complete answer.','Another exact source passage.','And the final reply.']
        book={'id':'a'*32,'source_hash':'a'*64,'title':'Fixture','author':'Author','language':'en',
            'chapters':[{'id':'c','blocks':[{'id':f'p{i}','kind':'paragraph','text':t}for i,t in enumerate(texts)]}]}
        producer.write(self.source/'prepared/book.json',book)
        self.ref=self.root/'reference.wav';sf.write(self.ref,np.sin(np.arange(rate)*.04)*.08,rate,subtype='PCM_16')
        units=[{'id':f'u{i:04d}','chapter_id':'c','source_id':f'p{i}','kind':'paragraph','start':0,'end':len(t),
            'text':t,'mode':'narrator','voice':'narrator_baseline','mood':'calm',
            'palette_reference':{'path':str(self.ref),'audio_sha256':producer.file_digest(self.ref),'text':'Private reviewed reference',
                'reviewed':True,'voice_family':'narrator_baseline','mood':'calm'}}for i,t in enumerate(texts)]
        assigned={'language':'English','title':'Fixture','author':'Author','source_sha256':producer.file_digest(self.source/'prepared/book.json'),
            'source_epub_sha256':'a'*64,'cast':{},'units':units}
        producer.write(self.source/'analysis/assigned.json',assigned)
        producer.write(self.source/'analysis/structure.request.json',{'fixture':'exact full source'})
        producer.write(self.source/'analysis/structure.answer.json',{'request_sha256':producer.file_digest(self.source/'analysis/structure.request.json'),
            'answer':{f'p{i}':[{'text':t,'mode':'narrator'}]for i,t in enumerate(texts)}})
        original=producer.feedback.source_store
        with patch.object(producer.feedback,'source_store',side_effect=lambda root:original(root,self.root/'isolated-profile.sqlite3')):
            assignment=producer.prepare(self.source)
        self.intro=self.root/'intro.wav';sf.write(self.intro,np.repeat((np.sin(np.arange(8*rate)*.02)*.01)[:,None],2,axis=1),rate,subtype='PCM_16')
        self.bed=self.root/'bed.wav';sf.write(self.bed,np.sin(np.arange(20*12000)*.02)*.08,12000,subtype='PCM_16')
        self.ending=self.root/'ending.wav';sf.write(self.ending,np.repeat((np.sin(np.arange(24*48000-64)*.01)*.06)[:,None],2,axis=1),48000,subtype='PCM_16')
        model=self.root/'model.gguf';model.write_bytes(b'Synthetic model, never loaded')
        runner=self.root/'runner.exe';runner.write_bytes(b'Synthetic runner, never executed')
        music={}
        for kind,path in(('bed',self.bed),('ending',self.ending)):
            signature={'fixture':'synthetic prerecorded instrumental evidence; no ASR invocation'}
            check={'audio_sha256':producer.file_digest(path),'status':'no_words_detected','heard':'','passed':True,
                'verifier':signature,'verifier_sha256':producer.digest(signature)}
            receipt={'audio_sha256':producer.file_digest(path),'quality_passed':True,'cue':{'kind':kind,'seconds':24},
                'spec':{'style':'lute and wooden flute'}}
            check_path=self.root/(kind+'-check.json');receipt_path=self.root/(kind+'-receipt.json')
            producer.write(check_path,check);producer.write(receipt_path,receipt)
            music[kind]={'path':str(path),'sha256':producer.file_digest(path),'check_path':str(check_path),
                'check_sha256':producer.file_digest(check_path),'receipt_path':str(receipt_path),'receipt_sha256':producer.file_digest(receipt_path)}
        manifest={'speech_model':{'path':str(model),'sha256':producer.file_digest(model)},
            'runner':{'path':str(runner),'sha256':producer.file_digest(runner),'dlls':{}},'sampling':producer.SAMPLING,'seed':42,
            'effects_policy':'disabled','music':music,'opening':{'sha256':producer.file_digest(self.intro)},'ffmpeg':str(FFMPEG)}
        frozen={'assignment':assignment,'manifest':manifest};producer.write(self.source/'manifest.json',manifest);producer.write(self.source/'frozen.json',frozen)
        self.config={'schema':1,'policy':producer.POLICY,'source':str(self.source),'source_generation':producer.digest(frozen),
            'frozen':frozen,'dependencies':producer.pins(),'post_processing':'off','music_volume_scale':.9,'maximum_technical_attempts':3,
            'player_sha256':producer.file_digest(producer.native.assets.PLAYER_SOURCE)}
        key=producer.digest(self.config);self.job=self.output/'jobs'/key;producer.write(self.job/'config.json',self.config)
        producer.write(self.output/'current-job.json',{'job':key,'config_sha256':key})
        specs=[producer.request(self.config,u,i,0)for i,u in enumerate(assignment['units'])]
        self.session=self.job/'sessions'/producer.digest({'specs':specs,'launch':0});command=producer.native_command(self.config,self.session)
        producer.write(self.session/'request.json',{'requests':[s['request']for s in specs]})
        producer.write(self.session/'physical-start.json',{'specs':specs,'launch':0,'command':command,
            'sequence_sha256':producer.file_digest(self.session/'request.json')})
        self.owner={'pid':2147483647,'create_time':0.,'command':command}
        producer.write(self.session/'worker.json',self.owner);producer.write(self.session/'physical-result.json',{'returncode':0,'error':None})
        log=[]
        for index,spec in enumerate(specs):
            raw=self.session/'native'/(spec['request']['id']+'.wav');raw.parent.mkdir(exist_ok=True)
            sf.write(raw,np.sin(np.arange(6001+index*2401)*.045)*.1,rate,subtype='PCM_16')
            log.append(f'audio_out={raw}\nmetrics[{spec["request"]["id"]}].audio_duration_ms=250\n')
        (self.session/'native.log').write_text(''.join(log),encoding='utf8');producer.collect(self.job,self.session)
        self.patch(producer.native.assets,'opening',return_value=self.intro)
        producer.publish(self.output,self.job,self.config);producer.state(self.output,self.job,self.config,stage='stopped',worker_running=False)
        producer.write(self.output/'supervision.json',{'policy':producer.POLICY,'stage':'render','exit_code':0,'worker_running':False,
            'worker':self.owner,**producer.UNCHECKED})
        self.guard=self.patch(producer.native,'model_guard',return_value=(None,None))
        for obj,name in((producer,'publish'),(producer,'configure'),(producer.feedback,'source_store'),(producer.providers,'call'),(producer,'session_run')):
            self.patch(obj,name,side_effect=AssertionError('Verifier may not prepare, publish, synthesize or call providers'))

    def patch(self,obj,name,**kwargs):
        scope=patch.object(obj,name,**kwargs);value=scope.start();self.addCleanup(scope.stop);return value

    def test_real_reconstruction_decode_and_export_receipt_agree(self):
        receipt,report=verify.verify(self.source,self.output)
        self.guard.assert_called_once()
        self.assertEqual(receipt['checks'],{k:True for k in verify.CHECKS});self.assertEqual(receipt['total_units'],4)
        self.assertLessEqual(report['pcm']['max_error_lsb'],1);self.assertTrue(report['mp3']['wav_encoding_identical'])
        self.assertAlmostEqual(report['timing']['ending_seconds'],(24*48000-64)/48000)
        self.assertEqual(report['timing']['ending_nominal_seconds'],24);self.assertEqual(report['recognition_status'],'not_checked')
        rows=producer.selected(self.job,self.config)
        _mix,expected,_timing=verify.reconstruct(self.config['frozen']['assignment'],rows,self.job,self.intro,self.bed,self.ending)
        actual=producer.read(self.output/'public/latest.json')['captions']
        self.assertTrue(any(a['start']!=b['start']or a['end']!=b['end']for a,b in zip(actual,expected,strict=True)))
        self.assertTrue(verify.captions_match(actual,expected))
        path=self.root/'private-integrity.json';producer.write(path,receipt)
        metadata={'title':'Fixture','author':'Author','language':'English','source':'https://example.org/public-story','extent':'complete'}
        pages.export_one_shot(self.source,self.output,'en',self.root/'public-stage',demo_id='en-fixture',metadata=metadata,integrity=path)

    def test_wrong_music_gain_or_opening_samples_fail_pcm_gate(self):
        rows=producer.selected(self.job,self.config);assignment=self.config['frozen']['assignment']
        expected,_,_=verify.reconstruct(assignment,rows,self.job,self.intro,self.bed,self.ending)
        for sample in(50,8*24000+1000):
            changed=expected.copy();changed[sample,0]+=.01
            with self.subTest(sample=sample),self.assertRaisesRegex(ValueError,'one LSB'):
                verify.compare_pcm(changed,self.output/'public/current.wav')

    def test_full_rounded_ending_cannot_be_omitted_changed_or_rebound(self):
        rows=producer.selected(self.job,self.config)
        expected,_,_=verify.reconstruct(self.config['frozen']['assignment'],rows,self.job,self.intro,self.bed,self.ending)
        changed=self.root/'omitted-ending.wav';sf.write(changed,expected[:-32],24000,subtype='PCM_16')
        with self.assertRaisesRegex(ValueError,'shape/rate/format'):verify.compare_pcm(expected,changed)
        edited=expected.copy();edited[-100:,0]+=.001;sf.write(changed,edited,24000,subtype='PCM_16')
        with self.assertRaisesRegex(ValueError,'one LSB'):verify.compare_pcm(expected,changed)
        self.ending.write_bytes(self.ending.read_bytes()[:-2]+b'XX')
        with self.assertRaises(ValueError):verify.verify(self.source,self.output)

    def test_valid_mp3_from_different_pcm_fails_binding_even_at_same_duration(self):
        path=self.root/'different.wav';data,rate=sf.read(self.output/'public/current.wav',dtype='float32',always_2d=True)
        data[240000:241000]*=.5;sf.write(path,data,rate,subtype='PCM_16')
        with self.assertRaisesRegex(ValueError,'deterministic encoding'):
            verify.verify_mp3(FFMPEG,path,self.output/'public/current.mp3',len(data)/rate)

    def test_native_tamper_or_false_checked_disclosure_holds(self):
        raw=next((self.session/'native').glob('*.wav'));old=raw.read_bytes();raw.write_bytes(old[:-2]+b'XX')
        with self.assertRaises(ValueError):verify.verify(self.source,self.output)
        raw.write_bytes(old);path=self.output/'public/latest.json';status=producer.read(path);status['recognition_status']='passed';producer.write(path,status)
        with self.assertRaisesRegex(ValueError,'unchecked disclosure'):verify.verify(self.source,self.output)

    def test_false_native_history_approval_cannot_hide_behind_honest_latest(self):
        path=self.job/'attempts/u0000/0.json';row=producer.read(path);row['machine_verified']=True;producer.write(path,row)
        self.assertIs(producer.read(self.output/'public/latest.json')['machine_verified'],False)
        with self.assertRaisesRegex(ValueError,'history misstates'):verify.verify(self.source,self.output)

    def recovery_fixture(self):
        start=producer.read(self.session/'physical-start.json');lines=[];completed=[]
        for index,spec in enumerate(start['specs']):
            identifier=spec['request']['id'];wav=self.session/'native'/(identifier+'.wav');fmt=producer.native.basic_audio(wav)
            lines.extend([f'request_index={index}',f'request_id={identifier}',f'audio_out={wav}',
                f'metrics[{identifier}].sample_rate=24000',f'metrics[{identifier}].channels=1',
                f'metrics[{identifier}].audio_duration_source=output',f'metrics[{identifier}].audio_duration_ms={fmt["duration"]*1000}',
                f'metrics[{identifier}].wall_ms=10'])
            completed.append({'unit':spec['source']['id'],'request_sha256':producer.digest(spec['request']),
                'audio':str(wav.relative_to(self.session)),'audio_sha256':producer.file_digest(wav),'format':fmt})
        clean=('\r\n'.join(lines)+'\r\n').encode();offset=clean.index(b'audio_out=')+15
        raw=clean[:offset]+verify.CUDA_NOISE+clean[offset:];(self.session/'native.log').write_bytes(raw)
        config={'implementation_sha256':'f'*64,'producer_sha256':producer.file_digest(Path(producer.__file__))}
        proof={'policy':verify.RECOVERY,'parser_sha256':config['implementation_sha256'],'producer_sha256':config['producer_sha256'],
            'physical_start_sha256':producer.file_digest(self.session/'physical-start.json'),
            'physical_result_sha256':producer.file_digest(self.session/'physical-result.json'),'log_sha256':pages.sha(raw),
            'normalized_log_sha256':pages.sha(clean),'removed_spans':[{'start':offset,'end':offset+len(verify.CUDA_NOISE),'bytes_hex':verify.CUDA_NOISE.hex()}],
            'completed':completed,'post_processing':'off','recognition_status':'not_checked'}
        path=self.session/'log-parser-recovery.json';producer.write(path,proof)
        for spec in start['specs']:
            receipt=self.job/'attempts'/spec['source']['id']/f'{spec["attempt"]}.json';row=producer.read(receipt)
            row.update(log_sha256=pages.sha(raw),log_parser_recovery_path=str(path.relative_to(self.job)),log_parser_recovery_sha256=producer.file_digest(path))
            producer.write(receipt,row)
        row=producer.read(self.job/'attempts/u0000/0.json')
        return {'config':config},row,path,proof,clean,raw

    def test_exact_diagnostic_recovery_preserves_raw_and_rejects_coherently_rehashed_bad_span(self):
        recovery,row,path,proof,clean,raw=self.recovery_fixture()
        with self.assertRaisesRegex(ValueError,'reviewed config'):verify.completion_log(self.job,row,None,{})
        self.assertEqual(verify.completion_log(self.job,row,recovery,{}),clean.decode())
        self.assertEqual((self.session/'native.log').read_bytes(),raw)
        proof['removed_spans'][0]['bytes_hex']='00'+proof['removed_spans'][0]['bytes_hex'][2:];producer.write(path,proof)
        row['log_parser_recovery_sha256']=producer.file_digest(path)
        with self.assertRaisesRegex(ValueError,'spans/parser/native proof'):verify.completion_log(self.job,row,recovery,{})

    def test_recovery_cannot_accept_failed_native_or_wrong_full_output_metrics(self):
        recovery,row,path,proof,clean,raw=self.recovery_fixture()
        producer.write(self.session/'physical-result.json',{'returncode':1,'error':None})
        with self.assertRaisesRegex(ValueError,'exit zero'):verify.completion_log(self.job,row,recovery,{})
        producer.write(self.session/'physical-result.json',{'returncode':0,'error':None})
        altered=raw.replace(b'.sample_rate=24000',b'.sample_rate=24001',1);(self.session/'native.log').write_bytes(altered)
        row['log_sha256']=pages.sha(altered)
        with self.assertRaisesRegex(ValueError,'metrics differ'):verify.completion_log(self.job,row,recovery,{})

    def test_explicit_recovery_anchor_is_bound_to_exact_source_and_current_job(self):
        pointer=producer.read(self.output/'current-job.json');path=self.root/'recovery/config.json'
        config={'policy':verify.RECOVERY,'original_job':pointer,'command_scope':str(self.output),
            'producer_sha256':producer.file_digest(Path(producer.__file__)),'implementation_sha256':'f'*64}
        producer.write(path,config);checksum=producer.file_digest(path)
        with self.assertRaisesRegex(ValueError,'together'):verify.recovery_binding(self.output,self.output,pointer,path,None)
        self.assertEqual(verify.recovery_binding(self.output,self.output,pointer,path,checksum)['config'],config)
        with self.assertRaisesRegex(ValueError,'another source/book/job'):verify.recovery_binding(self.source,self.output,pointer,path,checksum)
        foreign={**pointer,'job':'0'*64}
        with self.assertRaisesRegex(ValueError,'another source/book/job'):verify.recovery_binding(self.output,self.output,foreign,path,checksum)

    def test_nonterminal_owner_and_caption_timing_are_not_published_as_passed(self):
        path=self.output/'supervision.json';status=producer.read(path);status['worker_running']=True;producer.write(path,status)
        with self.assertRaisesRegex(ValueError,'terminal'):verify.verify(self.source,self.output)
        status['worker_running']=False;producer.write(path,status)
        latest_path=self.output/'public/latest.json';latest=producer.read(latest_path);latest['captions'][1]['start']+=.01
        latest['version']=producer.digest({'job':producer.digest(self.config),'captions':latest['captions'],'full':True})
        producer.write(latest_path,latest);producer.write(latest_path.parent/(latest['version']+'.json'),latest)
        with self.assertRaisesRegex(ValueError,'timeline identity'):verify.verify(self.source,self.output)


if __name__=='__main__':unittest.main()
