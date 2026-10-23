"""Book 2 saves and media routes use isolated fixtures, never user answers."""
import functools,hashlib,json,sys,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import serve_label_pilot as service

class LabelPilotServerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.original=service.ROOT;service.ROOT=self.root
        self.addCleanup(setattr,service,'ROOT',self.original)
        board=self.root/'training-work/character-rb2/fine-tune-all';board.mkdir(parents=True)
        (board/'summary.json').write_text(json.dumps({'candidates_sha256':'fixture'}))
        (board/'candidates.jsonl').write_text(json.dumps({'id':'a','book_id':'reality-benders-2'}))
        wave=self.root/'training-work/character-rb2/pilot/clips/a.wav';wave.parent.mkdir(parents=True);wave.write_bytes(bytes(range(256)))
        ref=self.root/'manual-labeling/reality-benders-1/audio/ref.wav';ref.parent.mkdir(parents=True);ref.write_bytes(bytes(range(256)))
        self.server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(service.Handler,directory=str(self.root/'training-work')))
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.url=f'http://127.0.0.1:{self.server.server_port}'
    def post(self,payload):
        req=urllib.request.Request(self.url+service.PAGE+'patch-review',json.dumps(payload).encode(),{'Content-Type':'application/json','Origin':self.url})
        with urllib.request.urlopen(req) as response:return json.load(response)
    def test_save_archives_answers_and_conflicts_preserve_them(self):
        payload={'schema':1,'candidates_sha256':'fixture','changes':{'a':{'before':None,'after':{'corrected_voice_id':'masculine_4','corrected_mood':'curious','bucket_comment':'actual voice note','boundary_end':True}}}}
        saved=self.post(payload)['review'];manual=self.root/'manual-labeling/reality-benders-2'
        raw=(manual/'review.json').read_bytes();self.assertEqual(json.loads(raw),saved)
        latest=json.loads((manual/'latest.json').read_text());self.assertEqual(latest['review_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertFalse((manual/'snapshots').exists())
        self.assertNotIn('bucket_review',saved['decisions']['a']);self.assertTrue(saved['decisions']['a']['boundary_end'])
        payload['changes']['a']['after']['corrected_mood']='calm'
        with self.assertRaises(urllib.error.HTTPError) as e:self.post(payload)
        self.assertEqual(e.exception.code,409);self.assertEqual((manual/'review.json').read_bytes(),raw)
    def test_media_ranges_and_restricted_paths(self):
        for path in ['/character-rb2/pilot/clips/a.wav','/manual-labeling/reality-benders-1/audio/ref.wav']:
            with urllib.request.urlopen(urllib.request.Request(self.url+path,headers={'Range':'bytes=64-95'})) as r:
                self.assertEqual(r.status,206);self.assertEqual(r.read(),bytes(range(64,96)))
        for path in ['/manual-labeling/reality-benders-1/review.json','/character-rb2/pilot/clips/%2e%2e/%2e%2e/secret.json']:
            with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.url+path)
            self.assertEqual(e.exception.code,404)
    def test_book1_board_and_saves_share_origin_but_keep_reviews_separate(self):
        board=self.root/'training-work/character-rb1/fine-tune-all';board.mkdir(parents=True)
        (board/'summary.json').write_text(json.dumps({'candidates_sha256':'book1-fixture'}))
        (board/'candidates.jsonl').write_text(json.dumps({'id':'b','book_id':'reality-benders-1'}))
        (board/'buckets.html').write_text('<h1>Book 1</h1>')
        audio=board/'precision-v2/clips/b.wav';audio.parent.mkdir(parents=True);audio.write_bytes(bytes(range(256)))
        with urllib.request.urlopen(self.url+'/character-rb1/fine-tune-all/buckets.html') as response:
            self.assertIn(b'Book 1',response.read())
        with urllib.request.urlopen(urllib.request.Request(self.url+'/character-rb1/fine-tune-all/precision-v2/clips/b.wav',headers={'Range':'bytes=64-95'})) as response:
            self.assertEqual(response.status,206);self.assertEqual(response.read(),bytes(range(64,96)))
        payload={'schema':1,'candidates_sha256':'book1-fixture','changes':{'b':{'before':None,'after':{'corrected_mood':'calm'}}}}
        req=urllib.request.Request(self.url+'/character-rb1/fine-tune-all/patch-review',json.dumps(payload).encode(),
                                  {'Content-Type':'application/json','Origin':'https://label.example','Host':'label.example','X-Forwarded-Proto':'https'})
        with urllib.request.urlopen(req) as response: saved=json.load(response)['review']
        self.assertEqual(saved['decisions']['b']['corrected_mood'],'calm')
        self.assertEqual(json.loads((self.root/'manual-labeling/reality-benders-1/review.json').read_bytes()),saved)
        self.assertFalse((self.root/'manual-labeling/reality-benders-2/review.json').exists())
    def test_book3_board_and_saves_use_their_own_archive(self):
        board=self.root/'training-work/character-rb3-full/fine-tune-all';board.mkdir(parents=True)
        (board/'summary.json').write_text(json.dumps({'candidates_sha256':'book3-fixture'}))
        (board/'candidates.jsonl').write_text(json.dumps({'id':'c','book_id':'reality-benders-3'}))
        (board/'index.html').write_text('<h1>Book 3</h1>')
        audio=self.root/'training-work/character-rb3/full/clips/c.wav';audio.parent.mkdir(parents=True);audio.write_bytes(bytes(range(256)))
        with urllib.request.urlopen(self.url+'/character-rb3-full/fine-tune-all/') as response:
            self.assertIn(b'Book 3',response.read())
        with urllib.request.urlopen(urllib.request.Request(self.url+'/character-rb3/full/clips/c.wav',headers={'Range':'bytes=64-95'})) as response:
            self.assertEqual(response.status,206);self.assertEqual(response.read(),bytes(range(64,96)))
        payload={'schema':1,'candidates_sha256':'book3-fixture','changes':{'c':{'before':None,'after':{'corrected_mood':'calm'}}}}
        req=urllib.request.Request(self.url+'/character-rb3-full/fine-tune-all/patch-review',json.dumps(payload).encode(),
                                  {'Content-Type':'application/json','Origin':self.url})
        with urllib.request.urlopen(req) as response:saved=json.load(response)['review']
        self.assertEqual(saved['decisions']['c']['corrected_mood'],'calm')
        self.assertEqual(json.loads((self.root/'manual-labeling/reality-benders-3/review.json').read_bytes()),saved)
        self.assertFalse((self.root/'manual-labeling/reality-benders-2/review.json').exists())
    def test_global_voice_family_description_is_conflict_safe_and_does_not_touch_clips(self):
        taxonomy=self.root/'training/voice_families.json';taxonomy.parent.mkdir(parents=True)
        taxonomy.write_text(json.dumps({'schema':1,'families':[
            {'id':f'family_{index}','label':f'Family {index}','group':'Fixture'} for index in range(20)]}))
        board=self.root/'training-work/character-rb2/fine-tune-all'
        clip_review={'schema':1,'candidates_sha256':'fixture','decisions':{'a':{'corrected_mood':'calm'}}}
        (board/'review-draft.json').write_text(json.dumps(clip_review));before=(board/'review-draft.json').read_bytes()
        with urllib.request.urlopen(self.url+service.VOICE_REGISTRY_PAGE+'data') as response:data=json.load(response)
        self.assertEqual(len(data['families']),20)
        payload={'schema':1,'narrator_id':'vlad-rimsky','registry_version':'vlad-rimsky-v1',
                 'family_id':'family_3','before_revision':0,'decision':{
                     'descriptors':{'perceived_gender':'masculine','perceived_age':'mature','voice_weight':'heavy',
                         'voice_depth':'low','brightness':'dark','roughness':'rough','voice_quality':'rough','otherness':'human'},
                     'casting_description':'Low, dry and textured.',
                     'character_fit':'Older authority figures.',
                     'avoid_for':'Keep separate from the smoother low family.'}}
        req=urllib.request.Request(self.url+service.VOICE_REGISTRY_PAGE+'save-decision',json.dumps(payload).encode(),
                                  {'Content-Type':'application/json','Origin':self.url})
        with urllib.request.urlopen(req) as response:saved=json.load(response)['review']
        self.assertEqual(saved['decisions']['family_3']['_revision'],1)
        self.assertEqual((board/'review-draft.json').read_bytes(),before)
        registry=self.root/'manual-labeling/voice-family-registries/vlad-rimsky-v1'
        self.assertEqual(json.loads((registry/'review.json').read_bytes()),saved)
        latest=json.loads((registry/'latest.json').read_bytes())
        self.assertEqual(latest['review_sha256'],hashlib.sha256((registry/'review.json').read_bytes()).hexdigest())
        with self.assertRaises(urllib.error.HTTPError) as conflict:
            urllib.request.urlopen(req)
        self.assertEqual(conflict.exception.code,409)
    def test_new_family_is_append_only_checksummed_and_does_not_touch_reviews(self):
        source=self.root/'training/voice_families.json';source.parent.mkdir(parents=True)
        source.write_text(json.dumps({'schema':1,'families':[
            {'id':f'family_{index}','label':f'Family {index}','group':'Fixture'} for index in range(20)],
            'legacy_aliases':{'old_family':'family_1'}}))
        registry=self.root/'manual-labeling/voice-family-registries/vlad-rimsky-v1';registry.mkdir(parents=True)
        review={'schema':1,'narrator_id':'vlad-rimsky','registry_version':'vlad-rimsky-v1',
                'decisions':{'family_1':{'casting_description':'Protected human decision','_revision':1}}}
        review_raw=(json.dumps(review,indent=2)+'\n').encode();(registry/'review.json').write_bytes(review_raw)
        (registry/'latest.json').write_text(json.dumps({'schema':1,'review_sha256':hashlib.sha256(review_raw).hexdigest()}))
        board_review=self.root/'training-work/character-rb2/fine-tune-all/review-draft.json'
        board_review.write_text(json.dumps({'decisions':{'a':{'corrected_mood':'calm'}}}))
        protected={path:path.read_bytes() for path in (registry/'review.json',board_review)}
        with urllib.request.urlopen(self.url+service.VOICE_REGISTRY_PAGE+'data') as response:before=json.load(response)
        self.assertEqual(before['revision'],0);self.assertEqual(len(before['families']),20)
        payload={'schema':1,'narrator_id':'vlad-rimsky','registry_version':'vlad-rimsky-v1',
                 'display_name':'Глубокий страж','group':'Fixture','id_preview':'custom_glubokiy_strazh',
                 'before_revision':before['revision'],'before_families_sha256':before['families_sha256']}
        request=urllib.request.Request(self.url+service.VOICE_REGISTRY_PAGE+'create-family',json.dumps(payload).encode(),
            {'Content-Type':'application/json','Origin':self.url})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status,201);created=json.load(response)
        family=created['created'];self.assertEqual(family['id'],'custom_glubokiy_strazh')
        self.assertEqual(family['synthesis_status'],'requires_pack_voice')
        self.assertEqual(len(created['taxonomy']['families']),21)
        canonical=registry/'families.json';latest=json.loads((registry/'latest.json').read_text())
        self.assertEqual(latest['families_sha256'],hashlib.sha256(canonical.read_bytes()).hexdigest())
        self.assertEqual(latest['review_sha256'],hashlib.sha256(review_raw).hexdigest())
        audit=json.loads(canonical.read_text(encoding='utf-8'))['audit'];self.assertEqual(audit[-1]['action'],'create_family')
        self.assertEqual(audit[-1]['previous_families_sha256'],before['families_sha256'])
        for path,raw in protected.items():self.assertEqual(path.read_bytes(),raw)
        frozen=canonical.read_bytes()
        with self.assertRaises(urllib.error.HTTPError) as duplicate:urllib.request.urlopen(request)
        self.assertEqual(duplicate.exception.code,409);self.assertEqual(canonical.read_bytes(),frozen)
        invalid={**payload,'display_name':'Another family','id_preview':'custom_another_family',
                 'before_revision':1,'before_families_sha256':latest['families_sha256'],'group':'Invented group'}
        bad=urllib.request.Request(self.url+service.VOICE_REGISTRY_PAGE+'create-family',json.dumps(invalid).encode(),
            {'Content-Type':'application/json','Origin':self.url})
        with self.assertRaises(urllib.error.HTTPError) as rejected:urllib.request.urlopen(bad)
        self.assertEqual(rejected.exception.code,400);self.assertEqual(canonical.read_bytes(),frozen)
    def test_nested_split_answers_are_archived_and_conflicting_tab_cannot_erase(self):
        original={'corrected_voice_id':'masculine_2','corrected_mood':'energetic','boundary_end':True}
        common={'schema':1,'candidates_sha256':'fixture'}
        saved=self.post({**common,'changes':{'a':{'before':None,'after':original}}})['review']['decisions']['a']
        split={**saved,'split_review':{'schema':1,'enabled':True,'training_approved':False,
            'cuts':[28],'parts':{'a~0-28':{'corrected_voice_id':'masculine_2','corrected_mood':'shouting'},
                                'a~28-58':{'corrected_voice_id':'narrator_baseline','corrected_mood':'neutral'}}}}
        result=self.post({**common,'changes':{'a':{'before':saved,'after':split}}})['review']
        answer=result['decisions']['a']
        for field,value in original.items():self.assertEqual(answer[field],value)
        manual=self.root/'manual-labeling/reality-benders-2/review.json'
        self.assertEqual(json.loads(manual.read_bytes()),result)
        rival={**split,'split_review':{**split['split_review'],'cuts':[20]}}
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post({**common,'changes':{'a':{'before':saved,'after':rival}}})
        self.assertEqual(e.exception.code,409)
        self.assertEqual(json.loads(manual.read_bytes()),result)

if __name__=='__main__':unittest.main()
