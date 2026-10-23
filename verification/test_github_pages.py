"""Publication boundaries; synthetic assets, no models or network access."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
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
        self.assertEqual(m.check(self.site,seo=False)['ready_demos'],1)
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

    def test_private_paths_are_rejected_and_public_source_link_is_allowed(self):
        for text in ('manual-labeling/fixture','C:\\Users\\private\\secret'):
            (self.site/'index.html').write_text(text,encoding='utf-8')
            with self.subTest(text=text),self.assertRaisesRegex(ValueError,'Private'):m.check(self.site)
        (self.site/'index.html').write_text('https://github.com/multipliedtwice/dogma',encoding='utf-8')
        self.assertEqual(m.check(self.site,seo=False)['ready_demos'],1)

    def test_caption_schema_cannot_carry_private_metadata(self):
        rows=copy.deepcopy(self.rows);rows[0]['reference_text']='Private example'
        with self.assertRaisesRegex(ValueError,'caption'):m.captions_valid(rows,40)
        rows=copy.deepcopy(self.rows);rows[0]['start']=0
        with self.assertRaisesRegex(ValueError,'caption'):m.captions_valid(rows,40)

    def test_relative_assets_cannot_escape_site(self):
        for path in ('../secret.mp3','/demos/en/file.mp3','demos/en/../../private', 'https://example.org/a.mp3'):
            with self.subTest(path=path),self.assertRaises(ValueError):m.local(self.site,path)


class LocalizedSEO(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.site=Path(self.temp.name)
        for name in ('index.html','ru/index.html','sitemap.xml','assets/social-en.png','assets/social-ru.png'):
            target=self.site/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/'website'/name,target)

    def change(self,name,before,after):
        path=self.site/name;text=path.read_text(encoding='utf-8')
        self.assertIn(before,text);path.write_text(text.replace(before,after,1),encoding='utf-8')

    def test_both_static_languages_have_consistent_crawl_and_share_metadata(self):
        self.assertEqual(m.check_seo(self.site)['canonical_pages'],2)
        russian=(self.site/'ru/index.html').read_text(encoding='utf-8')
        self.assertIn('Книги',russian)

    def test_russian_page_cannot_canonicalize_to_english(self):
        self.change('ru/index.html','rel="canonical" href="'+m.SITE_URL+'ru/"',
                    'rel="canonical" href="'+m.SITE_URL+'"')
        with self.assertRaisesRegex(ValueError,'canonical'):m.check_seo(self.site)

    def test_missing_return_language_link_is_rejected(self):
        self.change('ru/index.html','hreflang="en"','hreflang="de"')
        with self.assertRaisesRegex(ValueError,'language'):m.check_seo(self.site)

    def test_missing_html_language_or_duplicate_description_is_rejected(self):
        self.change('ru/index.html','<html lang="ru">','<html lang="en">')
        with self.assertRaisesRegex(ValueError,'language'):m.check_seo(self.site)
        self.change('ru/index.html','<html lang="en">','<html lang="ru">')
        self.change('index.html','</head>','<meta name="description" content="Duplicate"></head>')
        with self.assertRaisesRegex(ValueError,'duplicate'):m.check_seo(self.site)

    def test_changed_social_card_dimensions_are_rejected(self):
        path=self.site/'assets/social-ru.png';raw=bytearray(path.read_bytes());raw[16:20]=(500).to_bytes(4,'big');path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'1200'):m.check_seo(self.site)

    def test_sitemap_cannot_point_to_wrong_deployment_path(self):
        self.change('sitemap.xml',m.SITE_URL+'ru/',m.SITE_URL+'russian/')
        with self.assertRaisesRegex(ValueError,'Sitemap'):m.check_seo(self.site)


if __name__=='__main__':unittest.main()
