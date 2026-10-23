"""Local bucket review with conflict-safe edits and a protected canonical review."""
import argparse, functools, hashlib, json, math, os, re, threading, time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,unquote,urlsplit
from serve_book1_voice_evidence_atlas import ReviewHandler,ROOT
from book1_review_persistence import save_review,ReviewConflict
from label_clip_hls import handle as handle_clip_hls
from voice_family_registry import (RegistryConflict, TaxonomyConflict, create_family,
                                   read_review,
                                   save_decision as save_voice_family_decision,
                                   taxonomy as voice_family_taxonomy)

PAGE='/character-rb2/fine-tune-all/'
VOICE_REGISTRY_PAGE='/voice-family-registry/vlad-rimsky-v1/'
LISTENING='listening-check/'
ANSWERS=('calm','neutral','uncertain','neither')
BOOK_BOARDS={'/character-rb1/fine-tune-all/':'character-rb1',
             '/character-rb2/fine-tune-all/':'character-rb2',
             '/character-rb2-full/fine-tune-all/':'character-rb2-full',
             '/character-rb3-full/fine-tune-all/':'character-rb3-full'}
BOOK_AUDIO=('/character-rb1/fine-tune-all/precision-v2/',
            '/character-rb1/fine-tune-all/passage-audio/',
            '/character-rb2/pilot/clips/','/character-rb2/full/clips/',
            '/character-rb3/full/clips/','/character-rb3/labeling-full/clips/')
LISTENER_ROOT='elirm-iv-mvp'
LISTENER_GENERATIONS='elirm-iv-higgs-mvp'
LISTENER_CHAPTER=re.compile(r'c\d{4}\Z')
LISTENER_SLUG=re.compile(r'[A-Za-z0-9._-]+\Z')
RUSSIAN_WORD=re.compile(r'[\u0410-\u042f\u0430-\u044f\u0401\u0451\u0301]+(?:-[\u0410-\u042f\u0430-\u044f\u0401\u0451\u0301]+)*\Z')
RUSSIAN_VOWELS=set('\u0430\u0435\u0451\u0438\u043e\u0443\u044b\u044d\u044e\u044f\u0410\u0415\u0401\u0418\u041e\u0423\u042b\u042d\u042e\u042f')
FEEDBACK_LOCK=threading.Lock()
FILE_SHA_CACHE={}


def file_sha256(path):
    stat=path.stat();key=(str(path),stat.st_size,stat.st_mtime_ns)
    cached=FILE_SHA_CACHE.get(key)
    if cached:return cached
    value=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1024*1024):value.update(block)
    result=value.hexdigest()
    if len(FILE_SHA_CACHE)>32:FILE_SHA_CACHE.clear()
    FILE_SHA_CACHE[key]=result;return result


def listener_endpoint(page,path,endpoint):
    """Return the optional preview subpage for one tightly scoped endpoint."""
    prefix=page+LISTENER_ROOT+'/'
    if not path.startswith(prefix):return None
    parts=path[len(prefix):].strip('/').split('/')
    if not parts or parts[-1]!=endpoint:return None
    parents=parts[:-1]
    if len(parents)>1 or (parents and not LISTENER_SLUG.fullmatch(parents[0])):return None
    return parents[0] if parents else ''


def listener_snapshot(root,board_folder,subpage,chapter):
    if (board_folder!='character-rb2-full' or not isinstance(chapter,str)
            or not LISTENER_CHAPTER.fullmatch(chapter)):
        raise ValueError('Invalid listening chapter')
    page=root/'training-work'/board_folder/'fine-tune-all'/LISTENER_ROOT
    if subpage:page=page/subpage
    state=json.loads((page/'progress.json').read_text(encoding='utf-8'))
    generation_name=state.get('generation')
    if not isinstance(generation_name,str) or not LISTENER_SLUG.fullmatch(generation_name):
        raise ValueError('Invalid listening generation')
    generation=(root/'training-work'/LISTENER_GENERATIONS/generation_name).resolve()
    generation_root=(root/'training-work'/LISTENER_GENERATIONS).resolve()
    if not generation.is_relative_to(generation_root):raise ValueError('Invalid listening generation path')
    ready=state.get('chapters',{}).get(chapter)
    if not isinstance(ready,dict):raise ValueError('Chapter is not published')
    stream=ready.get('stream')
    if not isinstance(stream,str) or not stream.startswith('/character-rb2/full/clips/elirm-iv-mvp/'):
        raise ValueError('Chapter has no immutable audio')
    audio_path=(root/'training-work'/stream.lstrip('/')).resolve()
    audio_root=(root/'training-work/character-rb2/full/clips/elirm-iv-mvp').resolve()
    if not audio_path.is_relative_to(audio_root) or file_sha256(audio_path)!=ready.get('wav_sha256'):
        raise ValueError('Published audio changed')
    binding=ready.get('timeline_asset')
    if not isinstance(binding,dict):raise ValueError('Chapter has no immutable timeline')
    timeline_path=(generation/str(binding.get('file',''))).resolve()
    if (not timeline_path.is_relative_to((generation/chapter).resolve()) or
            binding.get('audio_sha256')!=ready.get('wav_sha256')):
        raise ValueError('Timeline is not bound to published audio')
    timeline_sha=file_sha256(timeline_path)
    if timeline_sha!=binding.get('sha256'):raise ValueError('Published timeline changed')
    rows=json.loads(timeline_path.read_text(encoding='utf-8'))
    if not isinstance(rows,list) or len(rows)!=ready.get('units'):
        raise ValueError('Published timeline coverage changed')
    return state,generation,ready,rows,timeline_sha


def listener_timeline(root,board_folder,subpage,chapter):
    state,_,ready,rows,timeline_sha=listener_snapshot(root,board_folder,subpage,chapter)
    fields=('id','start','end','source_id','text','speaker','structure')
    return {'generation':state['generation'],'chapter':chapter,'audio_sha256':ready['wav_sha256'],
            'timeline_sha256':timeline_sha,'seconds':ready['seconds'],
            'units':[{**{key:row.get(key) for key in fields},
                      'text_sha256':hashlib.sha256(row.get('text','').encode('utf-8')).hexdigest()} for row in rows]}


def _valid_stress_variant(word,variant):
    acute='\u0301'
    if not isinstance(variant,str) or variant.count(acute)!=1:return False
    if variant.replace(acute,'')!=word.replace(acute,''):return False
    position=variant.index(acute)
    return position>0 and variant[position-1] in RUSSIAN_VOWELS


def save_listener_feedback(root,board_folder,subpage,value):
    if not isinstance(value,dict):raise ValueError('Invalid feedback')
    chapter=value.get('chapter')
    state,generation,ready,rows,timeline_sha=listener_snapshot(root,board_folder,subpage,chapter)
    if (value.get('generation')!=state['generation'] or value.get('audio_sha256')!=ready['wav_sha256']
            or value.get('timeline_sha256')!=timeline_sha):
        raise ValueError('Feedback does not match the published audio and text')
    unit_id=value.get('unit_id')
    row=next((item for item in rows if item.get('id')==unit_id),None)
    if row is None or value.get('source_id')!=row.get('source_id'):raise ValueError('Invalid feedback unit')
    selected=value.get('selected_text');start=value.get('word_start');end=value.get('word_end')
    text=row.get('text')
    if (not isinstance(text,str) or not isinstance(selected,str) or len(selected)>100
            or type(start) is not int or type(end) is not int or not 0<=start<end<=len(text)
            or text[start:end]!=selected or not RUSSIAN_WORD.fullmatch(selected)):
        raise ValueError('Selected word is not an exact Russian word slice')
    expected_text_sha=hashlib.sha256(text.encode('utf-8')).hexdigest()
    if value.get('unit_text_sha256')!=expected_text_sha:raise ValueError('Feedback unit text changed')
    offset=value.get('audio_offset')
    if isinstance(offset,bool) or not isinstance(offset,(int,float)) or not math.isfinite(offset) or not 0<=offset<=ready['seconds']+.25:
        raise ValueError('Invalid audio offset')
    kind=value.get('kind');comment=value.get('comment','')
    if not isinstance(comment,str) or len(comment)>2000:raise ValueError('Invalid feedback comment')
    intended=value.get('intended_stress')
    if kind=='stress':
        if not _valid_stress_variant(selected,intended):raise ValueError('Invalid stress variant')
    elif kind=='other':
        if not comment.strip():raise ValueError('Other feedback requires a comment')
        intended=None
    else:raise ValueError('Invalid feedback kind')
    record={'schema':1,'kind':kind,'generation':state['generation'],'chapter':chapter,
            'unit_id':unit_id,'source_id':row.get('source_id'),'unit_text':text,
            'unit_text_sha256':expected_text_sha,'selected_text':selected,'word_start':start,'word_end':end,
            'intended_stress':intended,'comment':comment.strip(),'audio_offset':round(float(offset),3),
            'unit_start':row.get('start'),'unit_end':row.get('end'),'audio_sha256':ready['wav_sha256'],
            'timeline_sha256':timeline_sha,'saved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    encoded=(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
    folder=generation/'listener-feedback';folder.mkdir(parents=True,exist_ok=True)
    destination=folder/'reports.jsonl'
    with FEEDBACK_LOCK:
        with destination.open('ab') as stream:
            stream.write(encoded);stream.flush();os.fsync(stream.fileno())
    return {'saved':True,'report':{'kind':kind,'chapter':chapter,'unit_id':unit_id,'selected_text':selected}}

def listening_manifest(board_root):
    return json.loads((board_root/'fine-tune-all'/LISTENING/'manifest.json').read_text(encoding='utf-8'))

def listening_answers(board_root,experiment):
    path=board_root/'listening-check'/f'answers-{experiment}.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.is_file() else []

def listening_progress(board_root):
    """Which blind items already have an answer; never the answers or saved labels."""
    manifest=listening_manifest(board_root)
    return {'experiment_id':manifest['experiment_id'],
            'answered':sorted({row['item'] for row in listening_answers(board_root,manifest['experiment_id'])})}

def save_listening_answer(board_root,value):
    """Append a fresh blind answer to its own file; canonical labels are never touched."""
    manifest=listening_manifest(board_root)
    items={item['item']:item['clip_id'] for item in manifest['items']}
    if (not isinstance(value,dict) or value.get('experiment_id')!=manifest['experiment_id']
            or type(value.get('item')) is not int or items.get(value['item'])!=value.get('clip_id')
            or value.get('answer') not in ANSWERS):
        raise ValueError('Invalid listening answer')
    folder=board_root/'listening-check';folder.mkdir(parents=True,exist_ok=True)
    record={'experiment_id':manifest['experiment_id'],'item':value['item'],'clip_id':value['clip_id'],
            'answer':value['answer'],'saved_at':time.strftime('%Y-%m-%dT%H:%M:%S')}
    with (folder/f"answers-{manifest['experiment_id']}.jsonl").open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(record,ensure_ascii=False)+'\n')
    return {'saved':True,**listening_progress(board_root)}

RULEBOOK='mood-rulebook/'
VERDICTS=('approve','revise','unsure')

def rulebook(board_root):
    return json.loads((board_root/'fine-tune-all'/RULEBOOK/'rulebook.json').read_text(encoding='utf-8'))

def rulebook_progress(board_root):
    """The listener's own latest rulebook feedback per mood; clip labels are not read."""
    version=rulebook(board_root)['version']
    path=board_root/'mood-rulebook'/f'feedback-{version}.jsonl'
    latest={}
    for line in path.read_text(encoding='utf-8').splitlines() if path.is_file() else []:
        if line.strip():
            row=json.loads(line);latest[row['mood']]=row
    return {'version':version,'feedback':latest}

def save_rulebook_feedback(board_root,value):
    """Append rulebook feedback to its own file; it never changes clip labels."""
    book=rulebook(board_root)
    entries={entry['mood']:entry for entry in book['entries']}
    entry=entries.get(value.get('mood')) if isinstance(value,dict) else None
    texts=('meaning_revision','distinction','note')
    if (entry is None or value.get('version')!=book['version'] or value.get('verdict') not in VERDICTS
            or any(not isinstance(value.get(key,''),str) or len(value.get(key,''))>2000 for key in texts)
            or not isinstance(value.get('flagged_examples',[]),list)
            or not set(value.get('flagged_examples',[]))<={example['id'] for example in entry['examples']}):
        raise ValueError('Invalid rulebook feedback')
    folder=board_root/'mood-rulebook';folder.mkdir(parents=True,exist_ok=True)
    record={'version':book['version'],'mood':entry['mood'],'verdict':value['verdict'],
            **{key:value.get(key,'').strip() for key in texts},
            'flagged_examples':sorted(set(value.get('flagged_examples',[]))),'saved_at':time.strftime('%Y-%m-%dT%H:%M:%S')}
    with (folder/f"feedback-{book['version']}.jsonl").open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(record,ensure_ascii=False)+'\n')
    return {'saved':True,**rulebook_progress(board_root)}
class Handler(ReviewHandler):
    page=PAGE
    board_folder='character-rb2'
    audio_prefix='character-rb2/pilot'
    @property
    def workspace_root(self):return ROOT
    def allowed(self):
        path=unquote(urlsplit(self.path).path)
        if '\\' in path or '..' in Path(path).parts:return False
        return path.startswith((self.page,'/'+self.audio_prefix+'/clips/',*BOOK_BOARDS,*BOOK_AUDIO,'/manual-labeling/reality-benders-1/audio/'))
    def translate_path(self,path):
        clean=unquote(urlsplit(path).path)
        if clean.startswith('/manual-labeling/'):
            return str(ROOT/clean.lstrip('/'))
        return super().translate_path(path)
    def send_precompressed(self,head_only):
        if 'gzip' not in self.headers.get('Accept-Encoding','').casefold():return False
        source=Path(self.translate_path(self.path)).resolve()
        compressed=source.with_name(source.name+'.gz')
        board=(ROOT/'training-work'/self.board_folder/'fine-tune-all').resolve()
        if not source.is_relative_to(board) or not compressed.is_file():return False
        self.send_response(200)
        self.send_header('Content-Type',self.guess_type(str(source)))
        self.send_header('Content-Encoding','gzip')
        self.send_header('Vary','Accept-Encoding')
        self.send_header('Content-Length',str(compressed.stat().st_size))
        self.end_headers()
        if not head_only:
            with compressed.open('rb') as stream:
                while block:=stream.read(1024*1024):self.wfile.write(block)
        return True
    def do_GET(self):
        if handle_clip_hls(self):return
        parsed=urlsplit(self.path);path=parsed.path
        if path==VOICE_REGISTRY_PAGE+'data':
            try:self.reply(200,voice_family_taxonomy(ROOT))
            except (ValueError,OSError,json.JSONDecodeError) as e:self.reply(500,{'error':str(e)})
            return
        if path==VOICE_REGISTRY_PAGE+'review':
            try:self.reply(200,read_review(ROOT))
            except (ValueError,OSError,json.JSONDecodeError) as e:self.reply(500,{'error':str(e)})
            return
        subpage=listener_endpoint(self.page,path,'timeline')
        if subpage is not None:
            try:
                query=parse_qs(parsed.query,strict_parsing=True)
                if set(query)!={'chapter'} or len(query['chapter'])!=1:raise ValueError('Invalid timeline query')
                self.reply(200,listener_timeline(ROOT,self.board_folder,subpage,query['chapter'][0]))
            except (ValueError,OSError,json.JSONDecodeError) as e:self.reply(500 if isinstance(e,OSError) else 400,{'error':str(e)})
            return
        if path==self.page+LISTENING+'progress':
            try:self.reply(200,listening_progress(ROOT/'training-work'/self.board_folder))
            except (ValueError,OSError) as e:self.reply(400,{'error':str(e)})
            return
        if urlsplit(self.path).path==self.page+RULEBOOK+'progress':
            try:self.reply(200,rulebook_progress(ROOT/'training-work'/self.board_folder))
            except (ValueError,OSError) as e:self.reply(400,{'error':str(e)})
            return
        if self.allowed() and not urlsplit(self.path).path.lower().endswith('.wav') and self.send_precompressed(False):return
        super().do_GET()
    def do_HEAD(self):
        if handle_clip_hls(self,True):return
        if self.allowed() and not urlsplit(self.path).path.lower().endswith('.wav') and self.send_precompressed(True):return
        super().do_HEAD()
    def send_wave(self,head_only):
        path=Path(self.translate_path(self.path)).resolve()
        roots=[(ROOT/'training-work'/self.audio_prefix).resolve(),(ROOT/'manual-labeling/reality-benders-1/audio').resolve(),
               *((ROOT/'training-work'/prefix.lstrip('/')).resolve() for prefix in BOOK_AUDIO)]
        if not self.allowed() or not any(path.is_relative_to(root) for root in roots):self.send_error(404);return
        self.send_file_range(path,'audio/wav',head_only)
    def same_origin(self):
        origin=self.headers.get('Origin')
        # Accept saves only from the same address that served the page. This
        # works for loopback and a private LAN address without allowing a
        # different web origin to post labels into the local service.
        # A local HTTPS tunnel (ngrok) connects from loopback and serves the
        # same page over https; its forwarded scheme is trusted only there.
        if origin:
            parsed=urlsplit(origin)
            host=self.headers.get('Host','')
            tunnelled=self.client_address[0] in ('127.0.0.1','::1') and self.headers.get('X-Forwarded-Proto')=='https'
            if parsed.scheme not in ({'http','https'} if tunnelled else {'http'}) or parsed.netloc.casefold()!=host.casefold():
                raise ValueError('Unexpected request origin')
    def body(self,limit):
        size=int(self.headers.get('Content-Length','0'))
        if not 0<size<=limit:raise ValueError('Invalid request size')
        return json.loads(self.rfile.read(size))
    def do_POST(self):
        path=urlsplit(self.path).path
        if path==VOICE_REGISTRY_PAGE+'save-decision':
            try:
                self.same_origin()
                review=save_voice_family_decision(ROOT,self.body(100_000))
                self.reply(200,{'saved':True,'review':review})
            except RegistryConflict as e:self.reply(409,{'error':str(e)})
            except (ValueError,OSError,json.JSONDecodeError) as e:self.reply(500 if isinstance(e,OSError) else 400,{'error':str(e)})
            return
        if path==VOICE_REGISTRY_PAGE+'create-family':
            try:
                self.same_origin()
                created=create_family(ROOT,self.body(20_000))
                self.reply(201,{'created':created['families'][-1],'taxonomy':created})
            except TaxonomyConflict as e:self.reply(409,{'error':str(e)})
            except (ValueError,OSError,json.JSONDecodeError) as e:self.reply(500 if isinstance(e,OSError) else 400,{'error':str(e)})
            return
        subpage=listener_endpoint(self.page,path,'feedback')
        if subpage is not None:
            try:
                self.same_origin()
                self.reply(201,save_listener_feedback(ROOT,self.board_folder,subpage,self.body(16384)))
            except (ValueError,OSError,json.JSONDecodeError) as e:self.reply(500 if isinstance(e,OSError) else 400,{'error':str(e)})
            return
        if path==self.page+LISTENING+'answer':
            try:
                self.same_origin()
                self.reply(200,save_listening_answer(ROOT/'training-work'/self.board_folder,self.body(4096)))
            except (ValueError,OSError) as e:self.reply(500 if isinstance(e,OSError) else 400,{'error':str(e)})
            return
        if path==self.page+RULEBOOK+'feedback':
            try:
                self.same_origin()
                self.reply(200,save_rulebook_feedback(ROOT/'training-work'/self.board_folder,self.body(16384)))
            except (ValueError,OSError) as e:self.reply(500 if isinstance(e,OSError) else 400,{'error':str(e)})
            return
        target=next((folder for page,folder in BOOK_BOARDS.items() if path==page+'patch-review'),None)
        if path==self.page+'patch-review':target=self.board_folder
        if target is None:self.send_error(404);return
        try:
            self.same_origin()
            review=save_review(ROOT/'training-work'/target/'fine-tune-all',self.body(15_000_000),patch=True)
            self.reply(200,{'saved':True,'review':review})
        except ReviewConflict as e:self.reply(409,{'error':str(e),'conflicts':e.conflicts})
        except (ValueError,OSError) as e:self.reply(500 if isinstance(e,OSError) else 400,{'error':str(e)})
    def reply(self,status,value):
        raw=json.dumps(value,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

def main():
    global ROOT
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=1438)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--board-folder',default='character-rb2')
    parser.add_argument('--audio-prefix',default='character-rb2/pilot')
    parser.add_argument('--root',type=Path,default=ROOT,
                        help='Workspace root. Tests use an isolated temporary copy.')
    args=parser.parse_args()
    ROOT=args.root.resolve()
    board=(ROOT/'training-work'/args.board_folder/'fine-tune-all').resolve()
    audio=(ROOT/'training-work'/args.audio_prefix/'clips').resolve()
    if not board.is_relative_to((ROOT/'training-work').resolve()) or not audio.is_relative_to((ROOT/'training-work').resolve()):
        raise ValueError('Review paths must remain within training-work')
    page=f'/{args.board_folder}/fine-tune-all/'
    bound=type('BoundHandler',(Handler,),{'page':page,'board_folder':args.board_folder,'audio_prefix':args.audio_prefix})
    server=ThreadingHTTPServer((args.host,args.port),functools.partial(bound,directory=str(ROOT/'training-work')))
    print(f'Bucket review: http://{args.host}:{server.server_port}{page}',flush=True)
    server.serve_forever()

if __name__=='__main__':main()
