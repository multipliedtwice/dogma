"""Catalog-derived crawl metadata from synthetic ready entries; no media/network."""
import copy,json,re,shutil,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import build_website_metadata as builder
import github_pages as pages


class MultipleDemoMetadata(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.site=Path(temp.name)
        for name in('index.html','ru/index.html','assets/social-en.png','assets/social-ru.png'):
            target=self.site/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/'website'/name,target)
        self.catalog=[]
        for number,identifier in enumerate(('en','ru','ru-queen-spades','ru-malefactor','en-open-window','en-alice-tea-party')):
            locale=identifier[:2];hash_value=format(number+1,'064x')
            self.catalog.append({'id':identifier,'locale':locale,'language':{'en':'English','ru':'Russian'}[locale],
                'title':'Public '+identifier,'author':'Public Author','source':'https://example.org/'+identifier,
                'description':'An honest source excerpt.','extent':'excerpt'if identifier.endswith(('spades','party'))else'complete',
                'status':'ready','duration':360.5,'audio':'demos/'+locale+'/'+hash_value+'.mp3',
                'audio_sha256':hash_value,'captions':'demos/'+locale+'/captions-'+hash_value+'.json'})
        (self.site/'demos').mkdir();self.save()

    def save(self):
        (self.site/'demos/catalog.json').write_bytes(pages.encoded(self.catalog))

    def graph(self,locale):
        text=(self.site/builder.PAGES[locale]['path']).read_text(encoding='utf-8')
        return json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>',text).group(1))['@graph']

    def test_all_six_ready_books_have_same_language_stable_links_and_honest_extents(self):
        builder.build(self.site)
        self.assertEqual(pages.check_seo(self.site)['canonical_pages'],2)
        for locale in('en','ru'):
            graph=self.graph(locale);audio=[n for n in graph if n['@type']=='AudioObject']
            listing=next(n for n in graph if n['@type']=='ItemList')
            self.assertEqual(len(audio),3);self.assertEqual(listing['numberOfItems'],3)
            self.assertEqual(len(listing['itemListElement']),3)
            for row,node in zip((r for r in self.catalog if r['locale']==locale),audio):
                expected=builder.PAGES[locale]['url']+(''if row['id']==locale else'?demo='+row['id'])+'#listen'
                self.assertEqual(node['url'],expected);self.assertEqual(node['inLanguage'],locale)
                self.assertEqual(node['contentUrl'],builder.BASE+row['audio'])
                self.assertEqual(node['duration'],'PT360.5S');self.assertEqual(node['isBasedOn'],row['source'])
                label=('Отрывок'if locale=='ru'else'Excerpt')if row['extent']=='excerpt'else('Рассказ целиком'if locale=='ru'else'Complete story')
                self.assertTrue(node['description'].startswith(label))

    def test_pending_audio_has_no_audioobject_or_content_url(self):
        for row in self.catalog[2:]:row['status']='preparing'
        self.save();builder.build(self.site)
        for locale in('en','ru'):
            graph=self.graph(locale);self.assertEqual(len([n for n in graph if n['@type']=='AudioObject']),1)

    def test_featured_is_first_and_legacy_has_an_explicit_stable_selection_link(self):
        for row in self.catalog:row['featured']=row['id']in('en-open-window','ru-malefactor')
        self.save();builder.build(self.site);pages.check_seo(self.site)
        for locale,identifier in(('en','en-open-window'),('ru','ru-malefactor')):
            audio=[n for n in self.graph(locale)if n['@type']=='AudioObject']
            self.assertEqual(audio[0]['name'],'Public '+identifier)
            legacy=next(n for n in audio if n['name']=='Public '+locale)
            self.assertEqual(legacy['url'],builder.PAGES[locale]['url']+'?demo='+locale+'#listen')
        self.catalog[0]['featured']=True
        with self.assertRaisesRegex(ValueError,'one featured'):builder.audio_metadata('en',self.catalog)

    def test_catalog_updates_cannot_leave_stale_search_metadata(self):
        builder.build(self.site);self.catalog[-1]['title']='A different reviewed title';self.save()
        with self.assertRaisesRegex(ValueError,'AudioObject'):pages.check_seo(self.site)
        builder.build(self.site);pages.check_seo(self.site)

    def test_ready_metadata_rejects_external_or_cross_language_media(self):
        for audio in('https://example.org/audio.mp3','demos/ru/'+'a'*64+'.mp3'):
            catalog=copy.deepcopy(self.catalog);catalog[0]['audio']=audio
            with self.subTest(audio=audio),self.assertRaisesRegex(ValueError,'hashed media'):builder.audio_metadata('en',catalog)

    def test_jsonld_does_not_allow_title_to_escape_script_markup(self):
        self.catalog[2]['title']='Title </script><script>unsafe</script>';self.save();builder.build(self.site)
        page=(self.site/'ru/index.html').read_text(encoding='utf-8')
        self.assertIn('\\u003c/script>',page);self.assertNotIn('<script>unsafe</script>',page)
        pages.check_seo(self.site)


if __name__=='__main__':unittest.main()
