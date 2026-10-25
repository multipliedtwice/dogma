import copy
import json
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.jobs import Jobs,render_key
from rimsky import russian_preparation as prep
from rimsky.util import digest


class RuntimeRussianPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        blocks=[{'id':f'p{i}','text':('Нимрас'if i%2 else'Нимрасом')+' шел домой.'}for i in range(26)]
        self.book={'source_hash':'a'*64,'chapters':[{'id':'c1','blocks':blocks}]}
        self.generation={'analysis':{'pronunciation':{'policy':'auto-pronunciation-v1','names':{},'common':[]}},
            'segments':[{'id':b['id'],'source_id':b['id'],'chapter_id':'c1','text':b['text'],'spoken':b['text'],
                'speaker':'narrator','delivery':'neutral','start':0,'end':len(b['text']),'status':'pending'}for b in blocks]}
        class Store:
            root=Path(self.temp.name)
            def generation(inner,gid):return copy.deepcopy(self.generation)
            def execute(inner,sql,args):
                assert sql=='UPDATE generations SET analysis=? WHERE id=?'
                self.generation['analysis']=json.loads(args[0])
        self.jobs=Jobs.__new__(Jobs);self.jobs.store=Store();self.jobs._check=lambda:None
        self.calls=[]
        def call(operation,data):
            self.calls.append((operation,copy.deepcopy(data)))
            if operation=='prepare_russian_names':
                value={'book_sha256':data['book_sha256'],'input_sha256':digest({'inventory':data['inventory'],'names':data['names']}),
                    'proper_nouns':{'pn:Нимрас':'Ни́мрас'}}
            else:
                self.assertEqual(data['pronunciation']['proper_nouns'],{'pn:Нимрас':'Ни́мрас'})
                self.assertNotIn('prepared_units',data['pronunciation'])
                entries={}
                for unit in data['units']:
                    entry={'policy':prep.POLICY,'book_sha256':data['book_sha256'],'source':prep.source_binding(unit['source']),
                        'text_sha256':digest(unit['text'].encode()),'automatic_text':unit['text'].replace('Нимрас','Ни́мрас'),
                        'module_pins':{'fixture':'m'},'model_pins':{'fixture':'w'}}
                    entry['sound_sha256']=digest(prep.sound_binding(entry));entry['sha256']=digest(entry)
                    entries[prep.entry_key(unit['text'],unit['source'])]=entry
                value={'policy':prep.POLICY,'prepared_units':entries,'request_sha256':digest(data)}
            value['sha256']=digest(value);return value
        self.jobs._call=call

    def prepare(self):
        with patch.object(prep,'proper_inventory',return_value={'capitalized':['Нимрас','Нимрасом'],'coinages':[]}):
            return self.jobs._prepare_russian_chapter('current',self.book,'c1',prep.config('codex_cli'))

    def test_one_book_name_family_survives_batch_boundary_and_resume(self):
        spec=self.prepare()
        self.assertEqual([call[0]for call in self.calls],['prepare_russian_names','prepare_russian','prepare_russian'])
        self.assertEqual([len(c[1]['units'])for c in self.calls[1:]],[24,2])
        self.assertEqual(len(spec['prepared_units']),26)
        for unit in self.generation['segments']:
            binding={'source_id':unit['source_id'],'source_text':unit['text'],'source_start':0,'source_end':len(unit['text'])}
            entry=prep.resolve_entry(unit['text'],{**spec,'listener_source':binding})
            self.assertIn('Ни́мрас',entry['automatic_text'])
        self.book.update(position={'offset':50},generations=[{'id':'old'}],current_generation_id='changed')
        self.assertEqual(spec,self.prepare());self.assertEqual(len(self.calls),3)

    def test_changed_one_part_prepares_only_this_chapter_with_frozen_book_names(self):
        self.prepare();self.calls=[]
        unit=self.generation['segments'][-1];unit['spoken']+=' Снова.'
        self.prepare()
        self.assertEqual([c[0]for c in self.calls],['prepare_russian','prepare_russian'])
        self.assertEqual(self.calls[0][1]['pronunciation']['proper_nouns'],{'pn:Нимрас':'Ни́мрас'})

    def test_cross_book_worker_response_rejected_before_any_render(self):
        original=self.jobs._call
        def wrong(operation,data):
            result=original(operation,data)
            if operation=='prepare_russian':
                entry=next(iter(result['prepared_units'].values()));entry['book_sha256']='foreign'
                entry['sound_sha256']=digest(prep.sound_binding(entry));entry['sha256']=digest({k:v for k,v in entry.items()if k!='sha256'})
                result['sha256']=digest({k:v for k,v in result.items()if k!='sha256'})
            return result
        self.jobs._call=wrong
        with self.assertRaisesRegex(Exception,'different source book'):self.prepare()
        self.assertNotIn('prepared_units',self.generation['analysis']['pronunciation'])
        self.assertTrue(self.generation['analysis']['pronunciation']['proper_noun_preparation'])

    def test_prepared_nonverbal_cache_uses_same_sound_receipt_as_render_key(self):
        spec=self.prepare();unit=self.generation['segments'][0]
        binding={'source_id':unit['source_id'],'source_text':unit['text'],'source_start':0,'source_end':len(unit['text'])}
        spec={**spec,'listener_source':binding}
        plan={'backend':'nonverbal_asset','asset_sha256':'a'*64,'event':{'kind':'breath'}}
        pack={'speech':{'backend':'higgs_audio_tts_q8'},'files':{}}
        result={'sha256':'w'*64,'synthesis':{**plan,'plan_sha256':digest(plan)},
            'word_check':{'status':'nonverbal_not_spoken_text','audio_sha256':'w'*64,'asset_sha256':'a'*64,'event_kind':'breath'}}
        config={'_pronunciation':spec,'_speech_language':'Russian','post_processing':'off'}
        with patch('rimsky.speech_backend.is_higgs',return_value=True),patch('rimsky.higgs_backend.synthesis_plan',return_value=plan):
            key=render_key(pack,config,unit['text'],{},'neutral','Russian',0)
        plans=[({'text':unit['text'],'_pronunciation':spec},{},'neutral',True,None,key)]
        self.assertTrue(self.jobs._higgs_cached(result,plans,pack,config))
        raw=digest({'schema':5,'language':'Russian','plan':plan})
        self.assertNotEqual(raw,key)
        plans[0]=(*plans[0][:-1],raw)
        self.assertFalse(self.jobs._higgs_cached(result,plans,pack,config))

    def test_explicit_new_generation_clears_selected_freeze_resume_does_not(self):
        from rimsky.store import Store
        raw=('<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
            '<description><title-info><genre>sf</genre><author><first-name>Fixture</first-name><last-name>Author</last-name></author>'
            '<book-title>Fixture</book-title><lang>ru</lang></title-info></description><body><section><title><p>Chapter</p></title>'
            '<p>Он шел домой.</p></section></body></FictionBook>').encode()
        with closing(Store(Path(self.temp.name)/'library'))as store,patch('rimsky.opening.attach_opening',return_value={}):
            book=store.import_data(raw,'fixture.fb2');config={'pack_fingerprint':'fixture','russian_preparation':prep.config('local')}
            generation=store.create_generation(book['id'],config);chapter=generation['config']['chapters'][0]
            pronunciation={'policy':prep.POLICY,'names':{'name':{'stressed':'Ива́н','source':'listener'}},
                'prepared_chapters':{chapter:{'input_sha256':'old'},'unselected':{'input_sha256':'keep'}},
                'proper_noun_preparation':{'old':'freeze'},'prepared_units':{'old':'preserved until replaced'}}
            store.execute('UPDATE generations SET analysis=? WHERE id=?',(json.dumps({'complete':True,'approved':True,'pronunciation':pronunciation}),generation['id']))
            # Read/resume alone leaves all frozen evidence exactly intact.
            self.assertEqual(store.generation(generation['id'])['analysis']['pronunciation'],pronunciation)
            fresh=store.create_generation(book['id'],config,chapters=[chapter])
            self.assertEqual(fresh['id'],generation['id'])
            current=fresh['analysis']['pronunciation']
            self.assertNotIn(chapter,current['prepared_chapters']);self.assertIn('unselected',current['prepared_chapters'])
            self.assertNotIn('proper_noun_preparation',current)
            self.assertEqual(current['names'],pronunciation['names'])


if __name__=='__main__':unittest.main()
