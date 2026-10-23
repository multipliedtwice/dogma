"""Publication boundaries; synthetic assets, no models or network access."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('github_pages',ROOT/'scripts/github_pages.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class PagesBoundary(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.site=Path(self.temp.name)/'site';(self.site/'demos/en').mkdir(parents=True)
        for name in ('index.html','script.js','styles.css'):
            (self.site/name).write_text('Synthetic static asset',encoding='utf-8')
        audio=b'Synthetic MP3 fixture; not playable.'*40;checksum=m.sha(audio)
        (self.site/f'demos/en/{checksum}.mp3').write_bytes(audio)
        self.rows=[{'id':'u0000','source_id':'paragraph','text':'Original public-domain fixture.', 'start':8.0,'end':10.0}]
        self.caption=m.encoded(self.rows);self.caption_path=f'demos/en/captions-{m.sha(self.caption)}.json'
        (self.site/self.caption_path).write_bytes(self.caption)
        self.catalog=[{'id':'en','language':'English','title':'Fixture','author':'Author','status':'ready',
            'duration':40.0,'source':'https://example.org/story','audio':f'demos/en/{checksum}.mp3',
            'audio_sha256':checksum,'captions':self.caption_path},
            {'id':'ru','language':'Russian','title':'Pending','author':'Author','status':'preparing','source':'https://example.org/ru'}]
        self.save_catalog()
        credits={'schema':1,'music':{'model':'YuE2-3B','creator':'m-a-p','model_url':'https://huggingface.co/m-a-p/YuE2-3B',
            'conversion_url':'https://huggingface.co/audio-cpp/Yue2-3B-GGUF','license':'CC BY-NC 4.0',
            'license_url':'https://creativecommons.org/licenses/by-nc/4.0/'},'usage':'Non-commercial music.','verification':'Automated checks only.'}
        (self.site/'demos/credits.json').write_bytes(m.encoded(credits))

    def save_catalog(self):
        (self.site/'demos/catalog.json').write_bytes(m.encoded(self.catalog))

    def test_partial_catalog_can_only_publish_ready_english(self):
        self.assertEqual(m.check(self.site)['ready_demos'],1)
        self.catalog[1]['audio']=self.catalog[0]['audio'];self.save_catalog()
        with self.assertRaisesRegex(ValueError,'Unready'):m.check(self.site)

    def test_unexpected_dataset_or_model_file_is_rejected(self):
        for name in ('review.json','model.gguf','corpus.jsonl','old.mp3'):
            path=self.site/'demos/en'/name;path.write_bytes(b'Private fixture')
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'Unexpected file'):m.check(self.site)
            path.unlink()

    def test_audio_and_caption_tampering_is_rejected(self):
        audio=self.site/self.catalog[0]['audio'];original=audio.read_bytes();audio.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'audio checksum'):m.check(self.site)
        audio.write_bytes(original);(self.site/self.caption_path).write_bytes(b'[]')
        with self.assertRaisesRegex(ValueError,'Caption checksum'):m.check(self.site)

    def test_private_paths_and_private_repository_link_are_rejected(self):
        for text in ('manual-labeling/fixture','C:\\Users\\private\\secret',
                     'https://github.com/multipliedtwice/dogma',
                     '<a href="https://github.com/multipliedtwice/dogma/">Source</a>'):
            (self.site/'index.html').write_text(text,encoding='utf-8')
            with self.subTest(text=text),self.assertRaisesRegex(ValueError,'Private'):m.check(self.site)
        (self.site/'index.html').write_text('https://github.com/multipliedtwice/dogma-demo',encoding='utf-8')
        self.assertEqual(m.check(self.site)['ready_demos'],1)

    def test_caption_schema_cannot_carry_private_metadata(self):
        rows=copy.deepcopy(self.rows);rows[0]['reference_text']='Private example'
        with self.assertRaisesRegex(ValueError,'caption'):m.captions_valid(rows,40)
        rows=copy.deepcopy(self.rows);rows[0]['start']=0
        with self.assertRaisesRegex(ValueError,'caption'):m.captions_valid(rows,40)

    def test_relative_assets_cannot_escape_site(self):
        for path in ('../secret.mp3','/demos/en/file.mp3','demos/en/../../private', 'https://example.org/a.mp3'):
            with self.subTest(path=path),self.assertRaises(ValueError):m.local(self.site,path)


if __name__=='__main__':unittest.main()
