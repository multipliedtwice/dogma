"""The production listener records only immutable, audio-bound feedback."""
import base64,functools,hashlib,json,subprocess,sys,tempfile,threading,unittest,urllib.error,urllib.request
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import render_higgs_progressive_page as page
import serve_label_pilot as labels
import serve_listening_proxy as proxy


class ScriptParser(HTMLParser):
    def __init__(self):super().__init__();self.inside=False;self.scripts=[]
    def handle_starttag(self,tag,attrs):
        if tag=='script':self.inside=True;self.scripts.append('')
    def handle_endtag(self,tag):
        if tag=='script':self.inside=False
    def handle_data(self,data):
        if self.inside:self.scripts[-1]+=data


class ListenerFeedbackServerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.original=labels.ROOT;labels.ROOT=self.root;self.addCleanup(setattr,labels,'ROOT',self.original)
        page_dir=self.root/'training-work/character-rb2-full/fine-tune-all/elirm-iv-mvp/ch123';page_dir.mkdir(parents=True)
        generation=self.root/'training-work/elirm-iv-higgs-mvp/generation-1';chapter=generation/'c0001';chapter.mkdir(parents=True)
        self.row={'id':'c0001-u0001','start':1.0,'end':3.0,'source_id':'c0001-p0001','text':'Это проверка слова.','speaker':'narrator','structure':None,'word_status':'asr_exact_match'}
        raw=json.dumps([self.row],ensure_ascii=False,indent=2).encode();timeline=chapter/'bound.timeline.json';timeline.write_bytes(raw)
        self.timeline_sha=hashlib.sha256(raw).hexdigest();audio=b'fixture audio';self.audio_sha=hashlib.sha256(audio).hexdigest()
        media=self.root/'training-work/character-rb2/full/clips/elirm-iv-mvp/c0001.wav';media.parent.mkdir(parents=True);media.write_bytes(audio)
        progress={'generation':'generation-1','chapters':{'c0001':{'seconds':4.0,'units':1,'word_flags':0,
            'stream':'/character-rb2/full/clips/elirm-iv-mvp/c0001.wav','wav_sha256':self.audio_sha,
            'timeline_asset':{'file':'c0001/bound.timeline.json','sha256':self.timeline_sha,'audio_sha256':self.audio_sha}}}}
        (page_dir/'progress.json').write_text(json.dumps(progress),encoding='utf-8')
        handler=type('ListenerHandler',(labels.Handler,),{'page':'/character-rb2-full/fine-tune-all/',
            'board_folder':'character-rb2-full','audio_prefix':'character-rb2/full'})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(handler,directory=str(self.root/'training-work')))
        threading.Thread(target=self.server.serve_forever,daemon=True).start();self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.url=f'http://127.0.0.1:{self.server.server_port}/character-rb2-full/fine-tune-all/elirm-iv-mvp/ch123/'

    def post(self,payload):
        request=urllib.request.Request(self.url+'feedback',json.dumps(payload).encode(),
            {'Content-Type':'application/json','Origin':self.url.rstrip('/')})
        with urllib.request.urlopen(request) as response:return response.status,json.load(response)

    def payload(self):
        word='проверка';start=self.row['text'].index(word)
        return {'kind':'stress','generation':'generation-1','chapter':'c0001','audio_sha256':self.audio_sha,
            'timeline_sha256':self.timeline_sha,'unit_id':self.row['id'],'source_id':self.row['source_id'],
            'unit_text_sha256':hashlib.sha256(self.row['text'].encode()).hexdigest(),'selected_text':word,
            'word_start':start,'word_end':start+len(word),'intended_stress':'прове\u0301рка','comment':'','audio_offset':2.5}

    def test_timeline_and_append_only_feedback_are_bound_to_publication(self):
        with urllib.request.urlopen(self.url+'timeline?chapter=c0001') as response:timeline=json.load(response)
        self.assertEqual(timeline['audio_sha256'],self.audio_sha);self.assertEqual(timeline['units'][0]['text'],self.row['text'])
        status,result=self.post(self.payload());self.assertEqual(status,201);self.assertTrue(result['saved'])
        reports=self.root/'training-work/elirm-iv-higgs-mvp/generation-1/listener-feedback/reports.jsonl'
        saved=json.loads(reports.read_text(encoding='utf-8'));self.assertEqual(saved['intended_stress'],'прове\u0301рка')
        self.assertFalse((self.root/'manual-labeling').exists())

    def test_rejects_stale_hash_wrong_slice_and_invalid_stress(self):
        with self.assertRaises(urllib.error.HTTPError) as error:self.post({})
        self.assertEqual(error.exception.code,400)
        for field,value in [('audio_sha256','b'*64),('word_start',0),('intended_stress','проверка')]:
            payload=self.payload();payload[field]=value
            with self.assertRaises(urllib.error.HTTPError) as error:self.post(payload)
            self.assertEqual(error.exception.code,400)
        self.assertFalse((self.root/'training-work/elirm-iv-higgs-mvp/generation-1/listener-feedback/reports.jsonl').exists())

    def test_other_feedback_requires_a_bounded_comment(self):
        payload=self.payload();payload.update(kind='other',intended_stress=None,comment='  слышен щелчок  ')
        _,result=self.post(payload);self.assertTrue(result['saved'])
        payload['comment']=' '
        with self.assertRaises(urllib.error.HTTPError) as error:self.post(payload)
        self.assertEqual(error.exception.code,400)


class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.received.append((self.command,self.path,None,dict(self.headers)))
        if self.path=='/':raw=b'<html>production</html>';mime='text/html'
        elif self.path.startswith('/assets/'):raw=b'production asset';mime='text/javascript'
        elif self.path.startswith('/asset?'):raw=b'audio';mime='audio/wav'
        else:raw=b'not found';mime='text/plain'
        self.send_response(200 if raw!=b'not found' else 404);self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_POST(self):
        size=int(self.headers['Content-Length']);body=self.rfile.read(size)
        self.server.received.append((self.command,self.path,body,dict(self.headers)))
        request=json.loads(body)
        if request['op']=='library':
            raw=json.dumps([{'id':'a'*32,'generations':[{'id':'b'*24}]}]).encode()
        else:raw=b'{"saved":true}'
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def log_message(self,*args):pass


class ListeningProxyTest(unittest.TestCase):
    def setUp(self):
        self.runtime=ThreadingHTTPServer(('127.0.0.1',0),Upstream);self.runtime.received=[];threading.Thread(target=self.runtime.serve_forever,daemon=True).start()
        self.legacy=ThreadingHTTPServer(('127.0.0.1',0),Upstream);self.legacy.received=[];threading.Thread(target=self.legacy.serve_forever,daemon=True).start()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),proxy.Handler);self.server.upstream_port=self.legacy.server_port
        self.server.runtime_port=self.runtime.server_port;self.server.runtime_token='t'*48
        self.server.listener_book_id='';self.server.listener_generation_id=''
        self.server.authorization=b'Basic '+base64.b64encode(b'user:password');threading.Thread(target=self.server.serve_forever,daemon=True).start()
        for server in (self.server,self.runtime,self.legacy):self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        self.base=f'http://127.0.0.1:{self.server.server_port}';self.auth='Basic '+base64.b64encode(b'user:password').decode()

    def request(self,path,op=None,data=None,origin=None,auth=True,method=None):
        headers={};body=None
        if auth:headers['Authorization']=self.auth
        if origin is not None:headers['Origin']=origin
        if op is not None:
            body=json.dumps({'op':op,'data':data or {}}).encode();headers['Content-Type']='application/json'
        return urllib.request.Request(self.base+path,body,headers,method=method)

    def test_bare_listener_url_selects_only_runtime_book_and_serves_production_ui(self):
        opener=urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
        with opener.open(self.request(proxy.PRODUCTION_PAGE)) as response:
            self.assertEqual(response.read(),b'<html>production</html>')
            self.assertIn('listener=1',response.url)
            self.assertIn('book='+'a'*32,response.url);self.assertIn('generation='+'b'*24,response.url)
        with urllib.request.urlopen(self.request(proxy.PRODUCTION_PAGE+'assets/app.js')) as response:self.assertEqual(response.read(),b'production asset')
        self.assertTrue(any(method=='GET' and path=='/' for method,path,_,_ in self.runtime.received))

    def test_runtime_credentials_are_server_side_for_assets_and_api(self):
        with urllib.request.urlopen(self.request(proxy.PRODUCTION_PAGE+'asset?kind=audio&id='+'c'*64)) as response:self.assertEqual(response.read(),b'audio')
        asset=self.runtime.received[-1];self.assertEqual(asset[3]['Authorization'],'Bearer '+'t'*48)
        self.assertNotIn('Basic ',asset[3]['Authorization'])
        with urllib.request.urlopen(self.request(proxy.PRODUCTION_PAGE+'api','report_stress',{'selected_text':'word'},self.base)) as response:self.assertEqual(response.status,200)
        api=self.runtime.received[-1];self.assertEqual(api[0:2],('POST','/api'));self.assertEqual(api[3]['Authorization'],'Bearer '+'t'*48)
        self.assertNotIn('Origin',api[3])

    def test_only_listener_api_operations_and_safe_settings_are_forwarded(self):
        for op in ('library','book','generation','stress_reports','report_stress','save_position','save_reading_position','playback_streams'):
            with urllib.request.urlopen(self.request(proxy.PRODUCTION_PAGE+'api',op,{},self.base)) as response:self.assertEqual(response.status,200)
        with urllib.request.urlopen(self.request(proxy.PRODUCTION_PAGE+'api','update_settings',{'volume':.5,'follow':True},self.base)) as response:self.assertEqual(response.status,200)
        count=len(self.runtime.received)
        for op,data in (('generate',{}),('delete_book',{}),('update_settings',{'pack_path':'elsewhere'}),('update_settings',{})):
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(self.request(proxy.PRODUCTION_PAGE+'api',op,data,self.base))
            self.assertEqual(error.exception.code,403)
        self.assertEqual(len(self.runtime.received),count)

    def test_api_requires_basic_auth_same_origin_json_and_bounded_body(self):
        good=self.request(proxy.PRODUCTION_PAGE+'api','library',{},self.base)
        for request,status in (
            (self.request(proxy.PRODUCTION_PAGE+'api','library',{},self.base,auth=False),401),
            (self.request(proxy.PRODUCTION_PAGE+'api','library',{},'https://evil.example'),403),
            (self.request(proxy.PRODUCTION_PAGE+'api','library',{},None),403),
            (urllib.request.Request(self.base+proxy.PRODUCTION_PAGE+'api',b'{}',{'Authorization':self.auth,'Origin':self.base,'Content-Type':'text/plain'}),415),
            (self.request(proxy.PRODUCTION_PAGE+'feedback','library',{},self.base),405),
        ):
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
            self.assertEqual(error.exception.code,status)
        with urllib.request.urlopen(good) as response:self.assertEqual(response.status,200)

    def test_unrelated_and_traversal_paths_are_not_forwarded(self):
        before=(len(self.runtime.received),len(self.legacy.received))
        for path in ('/manual-labeling/review.json',proxy.PRODUCTION_PAGE+'../secret',
                     proxy.PRODUCTION_PAGE+'%2e%2e/secret','/assets%2f../secret'):
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(self.request(path))
            self.assertEqual(error.exception.code,404)
        self.assertEqual((len(self.runtime.received),len(self.legacy.received)),before)


class ProgressivePageTest(unittest.TestCase):
    def test_reader_and_feedback_javascript_is_syntax_valid(self):
        self.assertIn('timeline?chapter=',page.PAGE_HTML);self.assertIn("fetch('feedback'",page.PAGE_HTML)
        self.assertIn('reader-text',page.PAGE_HTML);self.assertIn('intended_stress',page.PAGE_HTML)
        parser=ScriptParser();parser.feed(page.PAGE_HTML)
        with tempfile.NamedTemporaryFile('w',suffix='.js',encoding='utf-8',delete=False) as output:
            output.write('\n'.join(parser.scripts));name=output.name
        try:subprocess.run(['node','--check',name],check=True,capture_output=True,text=True)
        finally:Path(name).unlink(missing_ok=True)


if __name__=='__main__':unittest.main()
