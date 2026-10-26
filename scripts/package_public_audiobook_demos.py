"""Export two completed demos as a latest-only static site and deterministic ZIP.

The default destination is ignored local publication/. This command never pushes
to GitHub. All completion/source checks run before output creation; private book,
model, reference and training directories are never traversed for export.
"""
from __future__ import annotations
import argparse
from html import escape
from html.parser import HTMLParser
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from zipfile import ZIP_STORED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT/'training-work/public-demos-20261023'
HASH = r'[a-f0-9]{64}'
BOOKS = {
    'en':{'title':'The Gift of the Magi','author':'O. Henry','year':1905,'language':'English',
          'edition':'Project Gutenberg ebook 7256', 'source':'https://www.gutenberg.org/ebooks/7256',
          'source_text':'https://www.gutenberg.org/cache/epub/7256/pg7256-images.html'},
    'ru':{'title':'Хамелеон','author':'Антон Павлович Чехов','year':1884,'language':'Russian',
          'edition':'Wikisource, version 2',
          'source':'https://ru.wikisource.org/wiki/Хамелеон_(Чехов)/Версия_2'},
}
MUSIC = {'model':'YuE2-3B','creator':'Multimodal Art Projection (m-a-p)',
         'model_url':'https://huggingface.co/m-a-p/YuE2-3B',
         'conversion_url':'https://huggingface.co/audio-cpp/Yue2-3B-GGUF',
         'license':'CC BY-NC 4.0','license_url':'https://creativecommons.org/licenses/by-nc/4.0/'}
PRIVATE_KEYS = frozenset({'palette_reference','voice_ref','reference_text','speech_model','runner',
                          'model_path','asr_model_path','original_spec','spec','prompt','system',
                          'provider_request','provider_requests','request','requests','dataset','datasets',
                          'training_data','training_audio','protected_labels','review_json','ffmpeg'})
PRIVATE_MARKERS = ('manual-labeling/','manual-labeling\\','training-work/','training-work\\',
                   'datasets/','datasets\\','corpora/','corpora\\','PRIVATE_TRAINING_PATH')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')


def json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


def safe_path(path):
    """Check lexical ancestors before resolving; Windows junctions count too."""
    path = Path(os.path.abspath(path))
    for node in [*reversed(path.parents),path]:
        try:
            details = node.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(details.st_mode) or getattr(details,'st_file_attributes',0) & getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400):
            raise ValueError('Symlinks/reparse points are not allowed: '+str(node))
    return path


def read_file(path,limit=256*1024*1024):
    path = safe_path(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Missing, non-regular or oversized publication input: '+str(path))
    return path.read_bytes()


def read_json(path):
    raw = read_file(path,16*1024*1024)
    return json.loads(raw.decode('utf-8')),raw


def clean_text(value):
    if any(marker.casefold() in value.casefold() for marker in PRIVATE_MARKERS):
        raise ValueError('Public artifact contains a private data-directory marker')
    if re.search(r'(?i)(?:(?<![a-z0-9])[a-z]:[\\/]|file://|\\\\[^\\\s]+\\|(?:^|[\s"\'])/(?:users|home|mnt|tmp|var|opt|workspace|private)(?:/|\\))',value):
        raise ValueError('Public artifact contains an absolute local path')


def clean_json(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if key.casefold() in PRIVATE_KEYS or key.casefold().startswith('private_'):
                raise ValueError('Public artifact contains a private payload field: '+key)
            clean_text(key)
            clean_json(item)
    elif isinstance(value,list):
        for item in value:
            clean_json(item)
    elif isinstance(value,str):
        clean_text(value)
        if value.startswith('/') or value.startswith('\\'):
            raise ValueError('Public artifact contains an absolute local path')


class Page(HTMLParser):
    # This is a publication boundary for the known generated player, not a
    # general HTML sanitizer. Unknown active/resource-bearing markup fails shut.
    ATTRS = {
        'html':{'lang'}, 'head':set(), 'body':set(), 'title':set(),
        'meta':{'charset','name','content'}, 'style':set(),
        'h1':set(), 'h2':set(), 'p':{'data-start','data-end','class'},
        'div':{'id','class'}, 'span':{'id','class'}, 'aside':{'id'},
        'main':set(), 'article':set(), 'footer':set(), 'br':set(),
        'a':{'href','download'}, 'audio':{'id','controls','src'},
        'script':{'type','id','src'},
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links,self.sources,self.scripts,self.configs = [],[],[],[]
        self.in_config = False
        self.config = []
        self.script_kind = None
        self.in_style = False
        self.css = []

    @staticmethod
    def check_css(value):
        # Escapes and comments must not disguise resource-fetching tokens.
        stripped = re.sub(r'/\*.*?\*/','',value,flags=re.S)
        if ('\\' in value or '/*' in stripped or re.search(r'\(|@|(?:behavior|-moz-binding)\s*:',stripped,re.I)
                or any(ord(char) < 32 and char not in '\r\n\t' for char in value)):
            raise ValueError('Unsafe CSS resource or executable expression in public page')

    def handle_starttag(self,tag,attrs):
        if tag not in self.ATTRS or len({key for key,_ in attrs}) != len(attrs):
            raise ValueError('Unexpected or duplicate markup in public page')
        for key,value in attrs:
            if key.startswith('on'):
                raise ValueError('Event-handler attributes are not allowed in public page')
            if key == 'style':
                self.check_css(value or '')
            elif key not in self.ATTRS[tag]:
                raise ValueError('Unexpected resource or attribute in public page: '+key)
        attrs = dict(attrs)
        if tag == 'meta' and not (set(attrs) == {'charset'} and str(attrs['charset']).lower() == 'utf-8'
                or set(attrs) == {'name','content'} and attrs['name'] == 'viewport'):
            raise ValueError('Unexpected metadata in public page')
        if 'href' in attrs:
            self.links.append(attrs['href'])
        if 'src' in attrs:
            self.sources.append(attrs['src'])
        if tag == 'script':
            if self.script_kind is not None:
                raise ValueError('Nested script in public page')
            if attrs == {'type':'application/json','id':'playback-config'}:
                self.script_kind = 'config'
                self.in_config,self.config = True,[]
            elif set(attrs) == {'src'} and attrs['src']:
                self.script_kind = 'external'
                self.scripts.append(attrs['src'])
            else:
                raise ValueError('Executable inline scripts are not allowed in public page')
        if tag == 'style':
            self.in_style,self.css = True,[]

    def handle_startendtag(self,tag,attrs):
        if tag in {'script','style'}:
            raise ValueError('Self-closing script/style is not allowed in public page')
        self.handle_starttag(tag,attrs)

    def handle_data(self,data):
        if self.in_config:
            self.config.append(data)
        elif self.script_kind == 'external' and data.strip():
            raise ValueError('Inline executable script content is not allowed in public page')
        if self.in_style:
            self.css.append(data)

    def handle_endtag(self,tag):
        if tag == 'script':
            if self.in_config:
                config = json.loads(''.join(self.config))
                if (not isinstance(config,dict)
                        or set(config) != {'generation','version','captions','opening_seconds','opening_sha256'}
                        or any(not isinstance(config.get(key),str) or not re.fullmatch(HASH,config[key])
                               for key in ('generation','version','opening_sha256'))
                        or type(config.get('opening_seconds')) is not int
                        or not isinstance(config.get('captions'),list)):
                    raise ValueError('Unexpected playback-config JSON shape')
                self.configs.append(config)
                self.in_config = False
            self.script_kind = None
        if tag == 'style' and self.in_style:
            self.check_css(''.join(self.css))
            self.in_style = False

    def close(self):
        super().close()
        if self.script_kind is not None or self.in_style:
            raise ValueError('Unclosed script/style in public page')


def named(value,pattern):
    if not isinstance(value,str) or not re.fullmatch(pattern,value):
        raise ValueError('Unexpected publication filename: '+str(value))
    return value


def checked_asset(public,name,checksum):
    if not re.fullmatch(HASH,str(checksum)):
        raise ValueError('Publication asset checksum is missing')
    raw = read_file(public/name)
    if sha(raw) != checksum:
        raise ValueError('Publication asset checksum mismatch: '+name)
    return raw


def public_metadata(key,value=None):
    if value is None:return dict(BOOKS[key])
    allowed={'title','author','language','source','source_text','year','edition','description','extent','featured'}
    if(not isinstance(value,dict)or set(value)-allowed or not {'title','author','language','source'}<=value.keys()
            or value['language']!={'en':'English','ru':'Russian'}.get(key)
            or any(not isinstance(value[k],str)or not value[k].strip()for k in('title','author','source'))):
        raise ValueError('Reviewed public demo metadata must match its explicit language')
    from urllib.parse import urlsplit
    source=urlsplit(value['source'])
    if source.scheme!='https'or not source.hostname or source.username or source.password:
        raise ValueError('Reviewed public source must be HTTPS')
    if value.get('extent','excerpt')not in('complete','excerpt'):raise ValueError('Invalid excerpt extent')
    if 'featured'in value and type(value['featured'])is not bool:raise ValueError('Featured selection must be boolean')
    clean_json(value)
    return dict(value)


def completed_book(folder,key,include_wav=False,*,expected_metadata=None):
    folder = safe_path(folder)
    public = safe_path(folder/'public')
    latest,latest_raw = read_json(public/'latest.json')
    version = named(latest.get('version'),HASH)
    complete,complete_raw = read_json(folder/'complete.json')
    version_record,version_raw = read_json(public/(version+'.json'))
    frozen,_ = read_json(folder/'frozen.json')
    state,_ = read_json(folder/'state.json')
    total = latest.get('total_units')
    if (latest.get('complete') is not True or type(total) is not int or total <= 0
            or latest.get('verified_units') != total or latest.get('open_recognition_flags') != 0
            or latest.get('pending_recognition_units') != 0 or latest.get('music_ready') is not True
            or latest.get('opening_seconds') != 8):
        raise ValueError('Both demos must be complete, fully verified, musical and have the eight-second opening')
    if complete != latest or version_record != latest:
        raise ValueError('Latest/version/strict completion receipts disagree')
    expected = public_metadata(key,expected_metadata)
    assignment = frozen['assignment']
    ids = [u['id'] for u in assignment['units']]
    if (latest.get('language') != expected['language'] or latest.get('title') != expected['title']
            or assignment.get('language') != expected['language'] or assignment.get('title') != expected['title']
            or assignment.get('author') != expected['author'] or len(ids) != total
            or len(set(ids)) != total or latest.get('frozen_generation') != sha(canonical(frozen))):
        raise ValueError('Completed demo identity differs from its frozen book')
    if ([u['id'] for u in state.get('units',[])] != ids
            or any(u.get('status') != 'verified' for u in state['units'])
            or state.get('counts',{}).get('verified') != total
            or any(v for k,v in state.get('counts',{}).items() if k != 'verified')
            or state.get('generation') != latest.get('generation')):
        raise ValueError('Strict source state is incomplete or differs from publication')
    report_name = named(latest.get('recognition_report'),'recognition-'+HASH+r'\.json')
    report,report_raw = read_json(public/report_name)
    report_hash = sha(canonical(report))
    if (latest.get('recognition_report_sha256') != report_hash or report_name != 'recognition-'+report_hash+'.json'
            or latest.get('generation') != sha(canonical({'frozen_generation':latest['frozen_generation'],
                                                          'recognition_report_sha256':report_hash}))
            or report.get('frozen_generation') != latest['frozen_generation']
            or report.get('verified_units') != total or report.get('open_recognition_flags') != 0
            or report.get('pending_units') != 0 or [u['id'] for u in report.get('units',[])] != ids
            or any(not u.get('selected') or u['selected'].get('passed') is not True for u in report['units'])):
        raise ValueError('Recognition disclosure does not prove current complete source coverage')
    captions = latest.get('captions',[])
    if [c['id'] for c in captions] != ids:
        raise ValueError('Caption coverage differs from frozen source units')
    end = 8.0
    for caption,row,unit in zip(captions,report['units'],assignment['units']):
        start,after = caption.get('start'),caption.get('end')
        if (type(start) not in (int,float) or type(after) not in (int,float)
                or not math.isfinite(start) or not math.isfinite(after) or start < end or after <= start
                or caption.get('raw_audio_sha256') != row['selected'].get('audio_sha256')
                or caption.get('text') != unit.get('text')):
            raise ValueError('Caption timing or selected raw audio differs from recognition evidence')
        end = after
    mp3 = named(latest.get('mp3'),version+r'\.mp3')
    audio = checked_asset(public,mp3,latest.get('mp3_sha256'))
    page_raw = read_file(public/'index.html',16*1024*1024)
    markup = page_raw.decode('utf-8')
    page = Page();page.feed(markup);page.close()
    if len(page.scripts) != 1 or len(page.configs) != 1:
        raise ValueError('Player must reference one hashed script and one playback configuration')
    player = named(page.scripts[0],'player-'+HASH+r'\.js')
    player_hash = player[len('player-'):-len('.js')]
    if player_hash != frozen.get('player_sha256'):
        raise ValueError('Player differs from the frozen implementation')
    player_raw = checked_asset(public,player,player_hash)
    config = page.configs[0]
    if (config.get('generation') != latest['frozen_generation'] or config.get('version') != version
            or config.get('captions') != captions or config.get('opening_seconds') != 8
            or not re.fullmatch(HASH,str(config.get('opening_sha256')))
            or config.get('opening_sha256') != frozen['manifest']['opening'].get('sha256')):
        raise ValueError('Player bookmark/version/captions differ from complete publication')
    wav = named(latest.get('wav'),version+r'\.wav')
    keep_wav = include_wav or wav in page.links+page.sources
    allowed = {mp3,player,report_name} | ({wav} if keep_wav else set())
    if any(value not in allowed for value in page.links+page.sources):
        raise ValueError('Page references an unselected, external or unexpected asset')
    if mp3 not in page.sources or report_name not in page.links:
        raise ValueError('Player audio or public recognition disclosure link is missing')
    projected = dict(latest)
    if not keep_wav:
        projected.pop('wav');projected.pop('wav_sha256')
    projected['export_projection'] = {'source_version':version,'wav_included':keep_wav,
                                     'omitted_fields':[] if keep_wav else ['wav','wav_sha256']}
    clean_json(latest);clean_json(report);clean_json(projected)
    clean_text(markup);clean_text(player_raw.decode('utf-8'))
    payload = {'index.html':page_raw,'latest.json':json_bytes(projected),version+'.json':json_bytes(projected),
               mp3:audio,player:player_raw,report_name:report_raw}
    if keep_wav:
        payload[wav] = checked_asset(public,wav,latest.get('wav_sha256'))
    # An atomic producer may have moved latest while this snapshot was read.
    if read_file(public/'latest.json') != latest_raw or read_file(folder/'complete.json') != complete_raw:
        raise ValueError('Publication changed during export snapshot')
    details = {**expected,'version':version,'mp3_sha256':sha(audio),'frozen_generation':latest['frozen_generation'],
               'source_latest_sha256':sha(latest_raw),'source_version_json_sha256':sha(version_raw),
               'source_completion_sha256':sha(complete_raw),'recognition_report_sha256':report_hash,
               'wav_included':keep_wav,'metadata_transform':'Remove omitted WAV fields and add export_projection; source publication, MP3, player, report and captions unchanged.'}
    return payload,details


def hub():
    entries = []
    for key in ('en','ru'):
        book = BOOKS[key]
        entries.append('<article><h2><a href="'+key+'/">'+escape(book['title'])+'</a></h2><p>'
                       +escape(book['author'])+' · '+str(book['year'])+' · '+escape(book['language'])+'</p><p>Source text: '
                       '<a href="'+escape(book['source'],quote=True)+'">'+escape(book['edition'])+'</a>.</p></article>')
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Dogma audiobook demos</title><style>body{max-width:800px;margin:3em auto;padding:1em;font:18px/1.6 system-ui;'
            'background:#151719;color:#eee}a{color:#aee}article{margin:2em 0}</style><main><h1>Dogma audiobook demos</h1>'
            '<p>Two complete audiobooks, machine-verified. No human listening approval is recorded.</p>'
            +''.join(entries)+'<footer><p>Original public-domain texts. Public-domain status is territorial; source links are retained for verification.</p>'
            '<p>Music, including the opening: <a href="'+MUSIC['model_url']+'">YuE2-3B</a> by '
            +escape(MUSIC['creator'])+', <a href="'+MUSIC['license_url']+'">CC BY-NC 4.0</a>; '
            '<a href="'+MUSIC['conversion_url']+'">audio.cpp GGUF conversion</a>. Music phrases were mixed with narration, with level changes and fades. '
            '<strong>This bundle includes non-commercial music and is for non-commercial use.</strong> Retain these credits and the license link when sharing.</p>'
            '</footer></main></html>').encode('utf-8')


def archive_bytes(files):
    stream = io.BytesIO()
    with ZipFile(stream,'w',compression=ZIP_STORED) as archive:
        for name,raw in sorted(files.items()):
            info = ZipInfo('dogma-demos/'+name,date_time=(1980,1,1,0,0,0))
            info.create_system = 3;info.external_attr = 0o100644 << 16
            archive.writestr(info,raw,compress_type=ZIP_STORED)
    return stream.getvalue()


def package(en_folder,ru_folder,output,include_wav=False):
    files,books = {'index.html':hub()},{}
    for key,folder in [('en',en_folder),('ru',ru_folder)]:
        selected,books[key] = completed_book(folder,key,include_wav)
        files.update({key+'/'+name:raw for name,raw in selected.items()})
    inventory = [{'path':name,'bytes':len(raw),'sha256':sha(raw)} for name,raw in sorted(files.items())]
    manifest = {'schema':1,'id':'dogma-public-audiobook-demos-v1','books':books,'music':MUSIC,
                'implementation_sha256':sha(Path(__file__).read_bytes()),'files':inventory,
                'inventory_excludes':['manifest.json','MANIFEST.sha256'],
                'privacy':'Latest public player/audio/disclosure assets only; no private book, model, reference or dataset directories.'}
    clean_json(manifest)
    files['manifest.json'] = json_bytes(manifest)
    files['MANIFEST.sha256'] = ''.join(sha(raw)+'  '+name+'\n' for name,raw in sorted(files.items())).encode('utf-8')
    zipped = archive_bytes(files)
    expected = {'site/'+name:raw for name,raw in files.items()}
    expected['dogma-public-audiobook-demos.zip'] = zipped
    output = safe_path(output)
    if output.exists():
        actual = {}
        for path in output.rglob('*'):
            safe_path(path)
            if path.is_file():
                actual[path.relative_to(output).as_posix()] = sha(read_file(path))
        if actual != {name:sha(raw) for name,raw in expected.items()}:
            raise ValueError('Finished export differs; use a new destination, nothing overwritten')
    else:
        output.parent.mkdir(parents=True,exist_ok=True)
        safe_path(output.parent)
        with tempfile.TemporaryDirectory(prefix='.dogma-export-',dir=output.parent) as temporary:
            staging = Path(temporary)/'bundle';staging.mkdir()
            for name,raw in expected.items():
                target = staging/name;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(raw)
            if staging.resolve().parent != Path(temporary).resolve() or output.parent != safe_path(output.parent):
                raise ValueError('Export staging/target left its intended directory')
            staging.rename(output)
    return {'site':str(output/'site'),'archive':str(output/'dogma-public-audiobook-demos.zip'),
            'manifest_sha256':sha(files['manifest.json']),'files':len(files),'archive_bytes':len(zipped)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--english',type=Path,default=DEFAULT/'en-gift-of-magi')
    parser.add_argument('--russian',type=Path,default=DEFAULT/'ru-chameleon')
    parser.add_argument('--output',type=Path,default=DEFAULT/'publication')
    parser.add_argument('--include-wav',action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(package(args.english,args.russian,args.output,args.include_wav),indent=2))
    except (OSError,ValueError,KeyError,TypeError) as error:
        parser.exit(1,str(error)+'\n')


if __name__ == '__main__':
    main()
