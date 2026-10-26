"""Synthetic complete publications only; no models, real demos or network calls."""
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('package_demos',ROOT/'scripts/package_public_audiobook_demos.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class StaticDemoPackaging(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def book(self,key,report_extra=None,wav_link=False):
        folder = self.root/key
        public = folder/'public';public.mkdir(parents=True)
        credit = package.BOOKS[key]
        player = b'/* synthetic self-contained player fixture */'
        player_hash = package.sha(player)
        player_name = 'player-'+player_hash+'.js'
        opening_hash = 'b'*64
        frozen = {'assignment':{**{k:credit[k] for k in ('title','author','language')},
                                  'units':[{'id':'u1','text':'A synthetic passage.',
                                            'palette_reference':{'path':'PRIVATE_TRAINING_PATH'}}]},
                  'manifest':{'opening':{'sha256':opening_hash}},'player_sha256':player_hash}
        frozen_generation = package.sha(package.canonical(frozen))
        report = {'frozen_generation':frozen_generation,'verified_units':1,'open_recognition_flags':0,'pending_units':0,
                  'units':[{'id':'u1','selected':{'passed':True,'audio_sha256':'a'*64}}],**(report_extra or {})}
        report_hash = package.sha(package.canonical(report))
        report_name = 'recognition-'+report_hash+'.json'
        generation = package.sha(package.canonical({'frozen_generation':frozen_generation,'recognition_report_sha256':report_hash}))
        version = package.sha(('synthetic-version-'+key).encode())
        mp3,wav = version+'.mp3',version+'.wav'
        mp3_raw,wav_raw = b'ID3 synthetic MP3 fixture '+key.encode(),b'RIFF synthetic WAV fixture '+key.encode()
        captions = [{'id':'u1','text':'A synthetic passage.','start':8.0,'end':10.0,'raw_audio_sha256':'a'*64}]
        latest = {'schema':1,'version':version,'generation':generation,'frozen_generation':frozen_generation,
                  'complete':True,'title':credit['title'],'language':credit['language'],'total_units':1,'verified_units':1,
                  'open_recognition_flags':0,'pending_recognition_units':0,'music_ready':True,'opening_seconds':8,
                  'captions':captions,'mp3':mp3,'mp3_sha256':package.sha(mp3_raw),'wav':wav,'wav_sha256':package.sha(wav_raw),
                  'recognition_report':report_name,'recognition_report_sha256':report_hash}
        config = {'generation':frozen_generation,'version':version,'captions':captions,'opening_seconds':8,'opening_sha256':opening_hash}
        markup = ('<!doctype html><meta charset="utf-8"><audio controls src="'+mp3+'"></audio>'
                  '<a href="'+mp3+'">MP3</a><a href="'+report_name+'">Recognition evidence</a>'
                  +('<a href="'+wav+'">WAV</a>' if wav_link else '')
                  +'<script type="application/json" id="playback-config">'+json.dumps(config)+'</script>'
                  '<script src="'+player_name+'"></script>')
        for name,value in [('latest.json',latest),(version+'.json',latest),(report_name,report)]:
            (public/name).write_bytes(package.json_bytes(value))
        (folder/'complete.json').write_bytes(package.json_bytes(latest))
        (folder/'frozen.json').write_bytes(package.json_bytes(frozen))
        (folder/'state.json').write_bytes(package.json_bytes({'generation':generation,'units':[{'id':'u1','status':'verified'}],
                                                               'counts':{'verified':1,'held':0,'pending':0}}))
        for name,raw in [('index.html',markup.encode()),(player_name,player),(mp3,mp3_raw),(wav,wav_raw),
                         ('obsolete-version.mp3',b'private old audio'),('obsolete-player.js',b'old script')]:
            (public/name).write_bytes(raw)
        return folder,latest

    def update_metadata(self,folder,latest):
        for path in [folder/'complete.json',folder/'public/latest.json',folder/'public'/(latest['version']+'.json')]:
            path.write_bytes(package.json_bytes(latest))

    def test_export_is_latest_only_reproducible_and_omits_wav_without_broken_metadata(self):
        en,en_meta = self.book('en');ru,_ = self.book('ru')
        before = (en/'public/latest.json').read_bytes()
        output = self.root/'publication'
        result = package.package(en,ru,output)
        archive = Path(result['archive']);original = archive.read_bytes()
        with ZipFile(archive) as zipped:
            names = zipped.namelist()
            self.assertFalse(any(name.endswith('.wav') or 'obsolete' in name or 'frozen.json' in name for name in names))
            self.assertFalse(any('manual-labeling' in name or 'dataset' in name for name in names))
            self.assertIn('dogma-demos/en/'+en_meta['mp3'],names)
            manifest = json.loads(zipped.read('dogma-demos/manifest.json'))
            for item in manifest['files']:
                self.assertEqual(package.sha(zipped.read('dogma-demos/'+item['path'])),item['sha256'])
            projected = json.loads(zipped.read('dogma-demos/en/latest.json'))
            self.assertNotIn('wav',projected);self.assertNotIn('wav_sha256',projected)
            self.assertFalse(projected['export_projection']['wav_included'])
            hub = zipped.read('dogma-demos/index.html').decode()
            self.assertIn('href="en/"',hub);self.assertIn('href="ru/"',hub)
            self.assertIn('non-commercial use',hub);self.assertIn(package.MUSIC['license_url'],hub)
        package.package(en,ru,output)
        result2 = package.package(en,ru,self.root/'second-export')
        self.assertEqual(original,Path(result2['archive']).read_bytes())
        self.assertEqual((en/'public/latest.json').read_bytes(),before)

    def test_both_books_must_be_complete_before_output_is_created(self):
        en,_ = self.book('en');ru,meta = self.book('ru')
        meta['complete'] = False;self.update_metadata(ru,meta)
        output = self.root/'publication'
        with self.assertRaisesRegex(ValueError,'Both demos must be complete'):
            package.package(en,ru,output)
        self.assertFalse(output.exists())

    def test_explicit_public_metadata_supports_additional_books_without_mutating_default_titles(self):
        en,_=self.book('en');before=dict(package.BOOKS['en'])
        metadata={**before,'description':'An excerpt from the original text.','extent':'excerpt'}
        payload,details=package.completed_book(en,'en',expected_metadata=metadata)
        self.assertEqual(details['extent'],'excerpt');self.assertEqual(details['description'],metadata['description'])
        self.assertEqual(package.BOOKS['en'],before)
        with self.assertRaisesRegex(ValueError,'identity'):
            package.completed_book(en,'en',expected_metadata={**metadata,'title':'Different book'})

    def test_reviewed_metadata_rejects_language_mismatch_private_payload_and_unsafe_sources(self):
        original={'title':'Public story','author':'Public author','language':'English','source':'https://example.org/story'}
        self.assertEqual(package.public_metadata('en',original),original)
        for changes in({'language':'Russian'},{'reference_text':'private labels'},
                        {'source':'file:///private'},{'source':'https://a:b@example.org/story'},
                        {'description':'manual-labeling/private'}, {'title':''}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):package.public_metadata('en',{**original,**changes})

    def test_tampered_mp3_or_completion_receipt_is_rejected(self):
        en,meta = self.book('en');ru,_ = self.book('ru')
        audio = en/'public'/meta['mp3'];original = audio.read_bytes();audio.write_bytes(b'wrong audio')
        with self.assertRaisesRegex(ValueError,'checksum mismatch'):
            package.package(en,ru,self.root/'publication')
        audio.write_bytes(original)
        complete = json.loads((en/'complete.json').read_text());complete['verified_units'] = 0
        (en/'complete.json').write_bytes(package.json_bytes(complete))
        with self.assertRaisesRegex(ValueError,'receipts disagree'):
            package.package(en,ru,self.root/'publication')

    def test_private_fields_and_absolute_paths_in_disclosure_are_rejected(self):
        for extra in [{'palette_reference':{'text':'protected training answer'}},
                      {'evidence':[{'path':'C:\\Users\\owner\\private.wav'}]},
                      {'evidence':[{'path':'/home/owner/private.wav'}]}]:
            with self.subTest(extra=extra),tempfile.TemporaryDirectory() as temporary:
                previous = self.root;self.root = Path(temporary)
                try:
                    en,_ = self.book('en',extra);ru,_ = self.book('ru')
                    with self.assertRaisesRegex(ValueError,'private|absolute'):
                        package.package(en,ru,self.root/'publication')
                    self.assertFalse((self.root/'publication').exists())
                finally:
                    self.root = previous

    def test_public_https_credits_allowed_but_embedded_local_drives_rejected(self):
        package.clean_json({'source':package.BOOKS['en']['source'],'music':package.MUSIC})
        for value in ['Local file: C:/Users/owner/clip.wav','path="D:\\private\\clip.wav"']:
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'absolute'):
                package.clean_text(value)

    def test_captions_must_retain_frozen_source_words(self):
        en,meta = self.book('en');ru,_ = self.book('ru')
        meta['captions'][0]['text'] = 'Changed source words.'
        self.update_metadata(en,meta)
        with self.assertRaisesRegex(ValueError,'Caption timing or selected raw audio'):
            package.package(en,ru,self.root/'publication')
        self.assertFalse((self.root/'publication').exists())

    def test_unexpected_reference_and_path_traversal_filename_rejected(self):
        en,meta = self.book('en');ru,_ = self.book('ru')
        page = en/'public/index.html';original = page.read_bytes()
        page.write_bytes(original+b'<a href="obsolete-version.mp3">Old take</a>')
        with self.assertRaisesRegex(ValueError,'unexpected asset'):
            package.package(en,ru,self.root/'publication')
        page.write_bytes(original)
        meta['mp3'] = '../private.mp3';self.update_metadata(en,meta)
        with self.assertRaisesRegex(ValueError,'Unexpected publication filename'):
            package.package(en,ru,self.root/'publication')

    def test_page_rejects_executable_markup_and_resource_injection(self):
        en,_ = self.book('en');ru,_ = self.book('ru')
        page = en/'public/index.html';original = page.read_bytes()
        injections = [
            '<script>alert("unexpected")</script>',
            '<script type="application/json" id="other">{}</script>',
            '<audio onerror="fetch(\'https://invalid.example\')"></audio>',
            '<style>body{background:url(https://invalid.example/collect)}</style>',
            '<style>@import "https://invalid.example/style.css";</style>',
            '<style>body{background:u/**/rl(local.png)}</style>',
            '<style>body{background:image-set("https://invalid.example/image" 1x)}</style>',
            '<p style="background:u\\72l(local.png)">Injected</p>',
            '<a href="https://invalid.example/collect">External</a>',
            '<meta http-equiv="refresh" content="0;url=https://invalid.example">',
            '<iframe src="https://invalid.example"></iframe>',
        ]
        for addition in injections:
            with self.subTest(addition=addition):
                page.write_bytes(original+addition.encode())
                with self.assertRaises(ValueError):
                    package.package(en,ru,self.root/'publication')
                self.assertFalse((self.root/'publication').exists())

    def test_config_requires_json_type_and_exact_expected_shape(self):
        en,_ = self.book('en');ru,_ = self.book('ru')
        page = en/'public/index.html';original = page.read_bytes()
        replacements = [
            (b'type="application/json"',b'type="text/javascript"'),
            (b'"opening_seconds": 8',b'"opening_seconds": true'),
            (b'"opening_seconds": 8',b'"extra": "ignored", "opening_seconds": 8'),
        ]
        for old,new in replacements:
            with self.subTest(replacement=new):
                self.assertIn(old,original);page.write_bytes(original.replace(old,new))
                with self.assertRaises(ValueError):
                    package.package(en,ru,self.root/'publication')

    def test_known_generated_inline_styles_are_allowed(self):
        en,_ = self.book('en');ru,_ = self.book('ru')
        page = en/'public/index.html'
        style = (b'<style>body{max-width:800px;margin:3em auto;padding:1em;font:18px/1.6 system-ui;'
                 b'background:#151719;color:#eee}audio{width:100%}a{color:#aee}p.active{background:#303b48}'
                 b'p[data-start]{cursor:pointer}</style><p style="color:#eee">Safe</p>')
        page.write_bytes(page.read_bytes()+style)
        package.package(en,ru,self.root/'publication')

    def test_referenced_wav_is_included_to_keep_page_links_valid(self):
        en,meta = self.book('en',wav_link=True);ru,_ = self.book('ru')
        result = package.package(en,ru,self.root/'publication')
        with ZipFile(result['archive']) as zipped:
            self.assertIn('dogma-demos/en/'+meta['wav'],zipped.namelist())
            self.assertTrue(json.loads(zipped.read('dogma-demos/en/latest.json'))['export_projection']['wav_included'])

    def test_different_finished_export_is_never_overwritten(self):
        en,_ = self.book('en');ru,_ = self.book('ru');output = self.root/'publication'
        result = package.package(en,ru,output);archive = Path(result['archive']);original = archive.read_bytes()
        (output/'unexpected.bin').write_bytes(b'preserved user data')
        with self.assertRaisesRegex(ValueError,'nothing overwritten'):
            package.package(en,ru,output)
        self.assertEqual(archive.read_bytes(),original)
        self.assertEqual((output/'unexpected.bin').read_bytes(),b'preserved user data')

    def test_reparse_point_is_rejected_before_reading_target(self):
        target = self.root/'junction-fixture';target.mkdir()
        original = Path.lstat
        def marked(path,*args,**kwargs):
            if path == target:
                return SimpleNamespace(st_mode=stat.S_IFDIR,st_file_attributes=0x400)
            return original(path,*args,**kwargs)
        with patch.object(Path,'lstat',marked):
            with self.assertRaisesRegex(ValueError,'reparse'):
                package.safe_path(target/'file.json')


if __name__ == '__main__':
    unittest.main()
