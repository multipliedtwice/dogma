"""Unchecked publication is explicit; synthetic physical evidence, no inference."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import github_pages as pages
import generate_public_demo_current as producer


class OneShotExport(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.output=self.root/'output';self.site=self.root/'site'
        self.metadata={'title':'Fixture story','author':'Public Author','language':'English',
            'source':'https://example.org/story','description':'A complete public-domain fixture.','extent':'complete'}
        reference=self.root/'private-reference.wav';reference.write_bytes(b'private reference is never published')
        text='Hello.';book={'source_hash':'a'*64,'chapters':[{'id':'c','blocks':[{'id':'p','text':text,'kind':'paragraph'}]}]}
        producer.write(self.source/'prepared/book.json',book)
        unit={'id':'u0000','source_id':'p','chapter_id':'c','kind':'paragraph','start':0,'end':len(text),
            'text':text,'spoken':text,'synthesis_text':text,
            'palette_reference':{'path':str(reference),'audio_sha256':producer.file_digest(reference),
                'text':'PRIVATE REFERENCE TEXT','reviewed':True}}
        assignment={k:self.metadata[k]for k in('title','author','language')}
        assignment.update(units=[unit],source_sha256=producer.file_digest(self.source/'prepared/book.json'),source_epub_sha256='a'*64)
        manifest={'speech_model':{'path':str(self.root/'private-model.gguf'),'sha256':'b'*64},
            'runner':{'path':str(self.root/'private-runner.exe'),'sha256':'c'*64},'sampling':producer.SAMPLING,'seed':42}
        frozen={'assignment':assignment,'manifest':manifest};producer.write(self.source/'frozen.json',frozen)
        self.config={'schema':1,'policy':producer.POLICY,'post_processing':'off','frozen':frozen,
            'source_generation':producer.digest(frozen),'dependencies':producer.pins()}
        key=producer.digest(self.config);self.job=self.output/'jobs'/key
        producer.write(self.job/'config.json',self.config)
        producer.write(self.output/'current-job.json',{'job':key,'config_sha256':key})
        spec=producer.request(self.config,unit,0,0);self.session=self.job/'sessions'/producer.digest(spec)
        producer.write(self.session/'request.json',{'requests':[spec['request']]})
        command=producer.native_command(self.config,self.session)
        producer.write(self.session/'physical-start.json',{'specs':[spec],'command':command,
            'sequence_sha256':producer.file_digest(self.session/'request.json')})
        producer.write(self.session/'worker.json',{'pid':2147483647,'create_time':0.,'command':command})
        producer.write(self.session/'physical-result.json',{'returncode':0,'error':None})
        raw=self.session/'native'/(spec['request']['id']+'.wav');raw.parent.mkdir()
        with wave.open(str(raw),'wb')as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(24000);stream.writeframes(bytes(4800))
        (self.session/'native.log').write_text(f'audio_out={raw}\nmetrics[{spec["request"]["id"]}].audio_duration_ms=100\n',encoding='utf8')
        producer.collect(self.job,self.session)
        self.chosen=producer.selected(self.job,self.config)[0]['selected']
        public=self.output/'public';public.mkdir()
        audio=b'SYNTHETIC MP3 FIXTURE, NOT A DECODE CLAIM.'*80
        (public/'current.mp3').write_bytes(audio);(public/'current.wav').write_bytes(raw.read_bytes())
        self.latest={'policy':producer.POLICY,'source_generation':self.config['source_generation'],
            **{k:self.metadata[k]for k in('title','author','language')},**producer.UNCHECKED,
            'version':'d'*64,'render_complete':True,'total_units':1,'rendered_units':1,'opening_seconds':8,
            'duration':34.,'speech_end':8.1,'mp3':'current.mp3','wav':'current.wav',
            'mp3_sha256':pages.sha(audio),'wav_sha256':producer.file_digest(public/'current.wav'),
            'physical_native_attempts':1,'technical_retries':0,
            'captions':[{'id':'u0000','source_id':'p','text':text,'start':8.,'end':8.1,
                'raw_audio_sha256':self.chosen['audio_sha256'],'recognition_status':'not_checked'}]}
        self.proof_path=self.root/'private-integrity.json';self.save_publication()
        for name in('index.html','script.js','styles.css'):
            target=self.site/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('Synthetic site',encoding='utf8')
        for obj,name in((producer,'configure'),(producer.feedback,'source_store'),(producer,'validate_preparation')):
            scope=patch.object(obj,name,side_effect=AssertionError('Export must be read-only and profile-independent'))
            scope.start();self.addCleanup(scope.stop)

    def save_publication(self):
        public=self.output/'public'
        producer.write(public/'latest.json',self.latest);producer.write(public/(self.latest['version']+'.json'),self.latest)
        self.proof={'schema':1,'policy':'public-demo-one-shot-integrity-v1','producer_policy':producer.POLICY,
            'config_sha256':producer.file_digest(self.job/'config.json'),'latest_sha256':producer.file_digest(public/'latest.json'),
            'mp3_sha256':self.latest['mp3_sha256'],'wav_sha256':self.latest['wav_sha256'],
            'source_generation':self.config['source_generation'],'total_units':1,
            'selected':[{'id':'u0000','receipt_sha256':producer.file_digest(self.job/'attempts/u0000/0.json'),
                'audio_sha256':self.chosen['audio_sha256']}],
            'checks':{k:True for k in('source_coverage','native_pcm','opening','music','mp3_decode')},
            'post_processing':'off','recognition_status':'not_checked','human_approval':False}
        producer.write(self.proof_path,self.proof)

    def export(self):
        return pages.export_one_shot(self.source,self.output,'en',self.site,demo_id='en-open-window',
            metadata=self.metadata,integrity=self.proof_path)

    def test_explicit_unchecked_projection_keeps_only_public_audio_and_caption_columns(self):
        self.metadata['featured']=True
        self.export();result=pages.check(self.site,seo=False);self.assertEqual(result['ready_demos'],1)
        catalog=pages.read(self.site/'demos/catalog.json');row=catalog[-1]
        self.assertEqual(row['id'],'en-open-window');self.assertEqual(row['extent'],'complete');self.assertIs(row['featured'],True)
        self.assertEqual(pages.read(self.site/row['captions']),[{k:self.latest['captions'][0][k]for k in pages.CAPTION_FIELDS}])
        for path in self.site.rglob('*'):
            if path.is_file():
                raw=path.read_bytes();self.assertNotIn(b'PRIVATE REFERENCE',raw);self.assertNotIn(b'private-model',raw)
                self.assertNotIn(b'config_sha256',raw)
        credits=pages.read(self.site/'demos/credits.json')['verification']
        self.assertIn('recognition and acoustic checks were not run',credits)
        self.assertIn('existing Russian demo received separate development',credits)

    def test_incomplete_or_falsely_verified_mode_cannot_be_projected(self):
        original=copy.deepcopy(self.latest)
        for changes in({'render_complete':False},{'recognition_status':'passed'},{'machine_verified':True},
                        {'human_approval':True},{'post_processing':'on'},{'human_approval':0}):
            self.latest={**original,**changes};self.save_publication()
            with self.subTest(changes=changes),self.assertRaisesRegex(ValueError,'unchecked mode'):self.export()
        self.assertFalse((self.site/'demos').exists())

    def test_missing_or_unbound_independent_checks_are_rejected(self):
        for change in('native_pcm','mp3_decode','opening','music','source_coverage'):
            proof=copy.deepcopy(self.proof);proof['checks'][change]=False;producer.write(self.proof_path,proof)
            with self.subTest(check=change),self.assertRaisesRegex(ValueError,'integrity proof'):self.export()
        proof=copy.deepcopy(self.proof);proof['checks']['opening']=1;producer.write(self.proof_path,proof)
        with self.assertRaisesRegex(ValueError,'integrity proof'):self.export()
        self.assertFalse((self.site/'demos').exists())

    def test_current_media_and_selected_receipt_tampering_fail_before_public_writes(self):
        audio=self.output/'public/current.mp3';saved=audio.read_bytes();audio.write_bytes(saved+b'tampered')
        with self.assertRaisesRegex(ValueError,'integrity proof'):self.export()
        audio.write_bytes(saved);path=self.job/'attempts/u0000/0.json';row=producer.read(path)
        row['audio_sha256']='0'*64;producer.write(path,row)
        with self.assertRaises(ValueError):self.export()
        self.assertFalse((self.site/'demos').exists())

    def test_caption_source_or_metadata_cannot_be_rebound(self):
        self.latest['captions'][0]['text']='Different source.';self.save_publication()
        with self.assertRaisesRegex(ValueError,'source/native'):self.export()
        self.latest['captions'][0]['text']='Hello.';self.save_publication();self.metadata['title']='Other story'
        with self.assertRaisesRegex(ValueError,'source identity'):self.export()

    def test_live_or_changed_worker_launch_is_rejected(self):
        with patch.object(producer,'live',return_value=True),self.assertRaisesRegex(ValueError,'worker'):self.export()
        worker=producer.read(self.session/'worker.json')
        import os
        worker.update(pid=os.getpid(),create_time=producer.psutil.Process().create_time())
        producer.write(self.session/'worker.json',worker)
        with patch.object(producer,'live',return_value=False),self.assertRaisesRegex(ValueError,'worker'):self.export()
        worker.update(pid=2147483647,create_time=0.)
        worker['command'][0]='different-runner'
        producer.write(self.session/'worker.json',worker)
        with self.assertRaisesRegex(ValueError,'worker'):self.export()

    def test_legacy_recognition_export_remains_a_separate_strict_path(self):
        import package_public_audiobook_demos as package
        with patch.object(package,'completed_book',side_effect=ValueError('Recognition receipt required'))as strict:
            with self.assertRaisesRegex(ValueError,'Recognition receipt required'):
                pages.export(self.output,'en',self.site,34.,demo_id='en-open-window',metadata=self.metadata)
        strict.assert_called_once();self.assertFalse((self.site/'demos').exists())


if __name__=='__main__':unittest.main()
