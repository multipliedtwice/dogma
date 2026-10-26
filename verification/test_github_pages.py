"""Publication boundaries; synthetic assets, no models or network access."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
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
        for path in ('../secret.mp3','/demos/en/file.mp3','demos/en/../../private', 'https://example.org/a.mp3',
                     'demos/en/..\\..\\private.mp3','demos/en/..','demos/ru/%2e%2e'):
            with self.subTest(path=path),self.assertRaises(ValueError):m.local(self.site,path)

    def additional(self,identifier,language):
        locale={'English':'en','Russian':'ru'}[language];folder=self.site/'demos'/locale;folder.mkdir(exist_ok=True)
        raw=('Synthetic MP3 '+identifier).encode()*100;checksum=m.sha(raw)
        (folder/(checksum+'.mp3')).write_bytes(raw)
        captions=m.encoded([{'id':'u0000','source_id':identifier,'text':'Synthetic public excerpt.','start':8.,'end':20.}])
        name='captions-'+m.sha(captions)+'.json';(folder/name).write_bytes(captions)
        return {'id':identifier,'language':language,'locale':locale,'title':identifier,'author':'Public author',
            'description':'A source-bound excerpt.','extent':'excerpt','source':'https://example.org/'+identifier,
            'status':'ready','duration':360.,'audio':'demos/'+locale+'/'+checksum+'.mp3','audio_sha256':checksum,
            'captions':'demos/'+locale+'/'+name}

    def test_six_demos_have_unique_ids_and_separate_language_assets(self):
        self.catalog[1]=self.additional('ru','Russian')
        self.catalog.extend(self.additional(identifier,language)for identifier,language in(
            ('ru-queen-spades','Russian'),('ru-malefactor','Russian'),('en-open-window','English'),('en-alice-tea-party','English')))
        self.save_catalog();result=m.check(self.site,seo=False)
        self.assertEqual(result['ready_demos'],6)
        self.assertEqual(len({r['id']for r in self.catalog}),6)

    def test_duplicate_or_missing_legacy_id_is_rejected(self):
        self.catalog.append(dict(self.catalog[0]));self.save_catalog()
        with self.assertRaisesRegex(ValueError,'unique'):m.check(self.site,seo=False)
        self.catalog=self.catalog[1:2];self.save_catalog()
        with self.assertRaisesRegex(ValueError,'existing'):m.check(self.site,seo=False)

    def test_featured_requires_one_ready_boolean_selection_per_locale(self):
        self.catalog[0]['featured']=True;self.save_catalog();m.check(self.site,seo=False)
        self.catalog.append({**self.additional('en-open-window','English'),'featured':True});self.save_catalog()
        with self.assertRaisesRegex(ValueError,'one featured'):m.check(self.site,seo=False)
        self.catalog.pop()
        for index,value in((1,True),(0,1)):
            self.catalog[index]['featured']=value;self.save_catalog()
            with self.assertRaisesRegex(ValueError,'Featured'):m.check(self.site,seo=False)
            self.catalog[index].pop('featured')

    def test_catalog_language_id_and_asset_scope_cannot_disagree(self):
        row=self.additional('ru-queen-spades','Russian');self.catalog.append(row)
        for changes in({'locale':'en'},{'id':'en-impostor'},{'language':'French'},
                        {'audio':self.catalog[0]['audio']},{'captions':self.catalog[0]['captions']}):
            self.catalog[-1]={**row,**changes};self.save_catalog()
            with self.subTest(changes=changes),self.assertRaisesRegex(ValueError,'language'):m.check(self.site,seo=False)

    def test_metadata_rejects_hidden_private_fields_and_untrusted_source_urls(self):
        for changes in({'private_labels':{}},{'source':'file:///private'},{'source':'https://name:password@example.org/a'},
                        {'extent':'all-errors-fixed'},{'description':' '}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):m.catalog_identity({**self.catalog[0],**changes})

    def test_export_adds_by_demo_id_preserving_both_legacy_entries_and_media(self):
        import package_public_audiobook_demos as package
        previous=copy.deepcopy(self.catalog)
        original={p:p.read_bytes()for p in(self.site/'demos/en').iterdir()}
        metadata={'title':'New story','author':'Public Author','language':'English','source':'https://example.org/new',
            'extent':'excerpt','description':'A reviewed excerpt.'}
        audio=b'Synthetic additional MP3.'*100
        latest={'speech_end':336.,'mp3':'selected.mp3','captions':self.rows}
        payload={'latest.json':m.encoded(latest),'selected.mp3':audio}
        with patch.object(package,'completed_book',return_value=(payload,metadata))as completed:
            m.export(Path('unused'),'en',self.site,360.,demo_id='en-open-window',metadata=metadata)
        completed.assert_called_once_with(Path('unused'),'en',expected_metadata=metadata)
        catalog=m.read(self.site/'demos/catalog.json')
        self.assertEqual(catalog[:2],previous);self.assertEqual(catalog[-1]['id'],'en-open-window')
        self.assertEqual({p:p.read_bytes()for p in original},original)
        self.assertEqual(m.check(self.site,seo=False)['ready_demos'],2)

    def test_additional_export_without_reviewed_identity_fails_before_any_write(self):
        before={p.relative_to(self.site).as_posix():p.read_bytes()for p in self.site.rglob('*')if p.is_file()}
        with self.assertRaisesRegex(ValueError,'reviewed public metadata'):
            m.export(Path('unused'),'en',self.site,360.,demo_id='en-new-book')
        self.assertEqual(before,{p.relative_to(self.site).as_posix():p.read_bytes()for p in self.site.rglob('*')if p.is_file()})


class LocalizedSEO(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.site=Path(self.temp.name)
        for name in ('index.html','ru/index.html','sitemap.xml','assets/social-en.png','assets/social-ru.png','demos/catalog.json'):
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
        self.change('ru/index.html','<html lang="ru"','<html lang="en"')
        with self.assertRaisesRegex(ValueError,'language'):m.check_seo(self.site)
        self.change('ru/index.html','<html lang="en"','<html lang="ru"')
        self.change('index.html','</head>','<meta name="description" content="Duplicate"></head>')
        with self.assertRaisesRegex(ValueError,'duplicate'):m.check_seo(self.site)

    def test_changed_social_card_dimensions_are_rejected(self):
        path=self.site/'assets/social-ru.png';raw=bytearray(path.read_bytes());raw[16:20]=(500).to_bytes(4,'big');path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'1200'):m.check_seo(self.site)

    def test_sitemap_cannot_point_to_wrong_deployment_path(self):
        self.change('sitemap.xml',m.SITE_URL+'ru/',m.SITE_URL+'russian/')
        with self.assertRaisesRegex(ValueError,'Sitemap'):m.check_seo(self.site)


if __name__=='__main__':unittest.main()
