"""Export reviewed demo assets locally; validate the standalone GitHub Pages site.

GitHub Actions validates and deploys only the static website directory. Export
needs the ignored local completed-book workspace and never copies private inputs.
"""
from __future__ import annotations
import argparse
from html.parser import HTMLParser
import hashlib
import json
import math
from pathlib import Path
import re
import stat
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
HASH = r'[a-f0-9]{64}'
PRIVATE = re.compile(r'(?i)manual-labeling[\\/]|training-work[\\/]|corpora[\\/]|datasets[\\/]|'
                     r'(?<![a-z0-9])[a-z]:[\\/]|file://|PRIVATE_TRAINING_PATH')
CATALOG_KEYS = {'id','language','title','author','status','duration','audio','captions','source','audio_sha256'}
CAPTION_FIELDS = ('id','source_id','text','start','end')
CAPTION_KEYS = set(CAPTION_FIELDS)
STATIC = {'index.html','ru/index.html','styles.css','script.js','theme.js','.nojekyll','favicon.svg',
          'assets/column.svg','assets/social-en.png','assets/social-ru.png','sitemap.xml'}
SITE_URL='https://multipliedtwice.github.io/dogma/'


class LandingPage(HTMLParser):
    def __init__(self):
        super().__init__();self.language=None;self.head=False;self.title=False;self.titles=[]
        self.metadata={};self.links=[];self.anchors=[];self.h1=0;self.jsonld=[];self.capture=False

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='html':self.language=attrs.get('lang')
        if tag=='head':self.head=True
        if tag=='h1':self.h1+=1
        if tag=='a':self.anchors.append(attrs)
        if not self.head:return
        if tag=='title':self.title=True;self.titles.append('')
        if tag=='meta':
            key=attrs.get('name',attrs.get('property'))
            self.metadata.setdefault(key,[]).append(attrs.get('content',''))
        if tag=='link':self.links.append(attrs)
        if tag=='script' and attrs.get('type')=='application/ld+json':
            self.capture=True;self.jsonld.append('')

    def handle_endtag(self,tag):
        if tag=='head':self.head=False
        if tag=='title':self.title=False
        if tag=='script':self.capture=False

    def handle_data(self,data):
        if self.title:self.titles[-1]+=data
        if self.capture:self.jsonld[-1]+=data


def check_seo(site):
    """Require crawlable, reciprocal localized documents and honest share data."""
    site=Path(site);urls={'en':SITE_URL,'ru':SITE_URL+'ru/','x-default':SITE_URL}
    for language,path in [('en','index.html'),('ru','ru/index.html')]:
        page=LandingPage();page.feed(safe(site/path).read_text(encoding='utf-8'));page.close()
        if page.language!=language or page.h1!=1 or len(page.titles)!=1 or not page.titles[0].strip():
            raise ValueError('Landing page needs its explicit language, one title and one H1')
        canonical=[r.get('href') for r in page.links if r.get('rel')=='canonical']
        alternates=[r for r in page.links if r.get('rel')=='alternate' and 'hreflang' in r]
        if (canonical!=[urls[language]] or len(alternates)!=3
                or {r['hreflang']:r.get('href') for r in alternates}!=urls):
            raise ValueError('Self-canonical and reciprocal language alternates must match both public pages')
        for code in ('en','ru'):
            if not any(a.get('hreflang')==code and a.get('lang')==code
                       and urljoin(urls[language],a.get('href',''))==urls[code] for a in page.anchors):
                raise ValueError('Visible language links must work without JavaScript')
        def one(key):
            values=page.metadata.get(key,[])
            if len(values)!=1 or not values[0].strip():raise ValueError('Missing or duplicate SEO metadata: '+str(key))
            return values[0]
        description=one('description');robots=one('robots').casefold().split(',')
        if 'index' not in robots or 'follow' not in robots or any(x in robots for x in ('noindex','nofollow')):
            raise ValueError('Public landing pages must allow indexing and following links')
        image=SITE_URL+f'assets/social-{language}.png'
        expected={'og:type':'website','og:site_name':'Dogma','og:url':urls[language],
                  'og:title':page.titles[0],'og:description':description,
                  'og:locale':{'en':'en_US','ru':'ru_RU'}[language],
                  'og:locale:alternate':{'en':'ru_RU','ru':'en_US'}[language],
                  'og:image':image,'og:image:type':'image/png','og:image:width':'1200','og:image:height':'630',
                  'twitter:card':'summary_large_image','twitter:title':page.titles[0],
                  'twitter:description':description,'twitter:image':image}
        if any(one(k)!=v for k,v in expected.items()):raise ValueError('Localized search/share metadata differs')
        one('og:image:alt');one('twitter:image:alt')
        raw=safe(site/f'assets/social-{language}.png').read_bytes()
        if (not raw.startswith(b'\x89PNG\r\n\x1a\n') or len(raw)>2*1024*1024
                or tuple(int.from_bytes(raw[i:i+4],'big') for i in (16,20))!=(1200,630)):
            raise ValueError('Social image must be a real 1200 by 630 PNG')
        try:
            structured=[json.loads(text) for text in page.jsonld]
            nodes=[node for value in structured for node in value.get('@graph',[value])]
        except (ValueError,TypeError,AttributeError) as error:raise ValueError('Invalid JSON-LD') from error
        if not any(node.get('@type')=='WebPage' and node.get('url')==urls[language]
                   and node.get('inLanguage')==language and node.get('name')==page.titles[0] for node in nodes):
            raise ValueError('JSON-LD must describe the actual localized page')
    try:
        tree=ET.parse(safe(site/'sitemap.xml'))
    except ET.ParseError as error:raise ValueError('Invalid XML sitemap') from error
    locations=[node.text for node in tree.findall('{http://www.sitemaps.org/schemas/sitemap/0.9}url/{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
    if len(locations)!=2 or set(locations)!={SITE_URL,SITE_URL+'ru/'}:
        raise ValueError('Sitemap must contain the two canonical landing pages')
    return {'languages':['en','ru'],'canonical_pages':2}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


def safe(path):
    path = Path(path).absolute()
    for node in [*reversed(path.parents),path]:
        if not node.exists() and not node.is_symlink():
            continue
        info = node.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0) & getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400):
            raise ValueError('Linked paths are not publication inputs: '+str(node))
    return path


def read(path):
    return json.loads(safe(path).read_text(encoding='utf-8'))


def local(site,value):
    if not isinstance(value,str) or not re.fullmatch(r'demos/(?:en|ru)/[^/]+',value):
        raise ValueError('Demo asset must be a relative, language-scoped local path')
    return safe(site/value)


def captions_valid(rows,duration):
    if not isinstance(rows,list) or not rows:
        raise ValueError('A ready demo needs nonempty captions')
    ids=set();end=8.0
    for row in rows:
        if (set(row)!=CAPTION_KEYS or not re.fullmatch(r'u\d{4}',row['id']) or row['id'] in ids
                or not isinstance(row['text'],str) or not row['text'].strip()
                or not isinstance(row['source_id'],str)
                or any(type(row[k]) not in (int,float) or not math.isfinite(row[k]) for k in ('start','end'))
                or row['start']<end-1e-6 or not row['start']<row['end']<=duration):
            raise ValueError('Invalid source caption or timing')
        ids.add(row['id']);end=row['end']


def check(site,*,seo=True):
    site=safe(site)
    catalog=read(site/'demos/catalog.json')
    if not isinstance(catalog,list) or {r.get('id') for r in catalog}!={'en','ru'} or len(catalog)!=2:
        raise ValueError('Expected one English and one Russian demo entry')
    allowed=STATIC|{'demos/catalog.json','demos/credits.json'}
    ready=0
    for row in catalog:
        if set(row)-CATALOG_KEYS or not {'id','language','title','author','status','source'}<=row.keys():
            raise ValueError('Unsupported catalog metadata')
        if row['status']=='preparing':
            if set(row)&{'audio','captions','duration','audio_sha256'}:
                raise ValueError('Unready demo must not expose audio or captions')
            continue
        if row['status']!='ready' or not CATALOG_KEYS<=row.keys():
            raise ValueError('Incomplete ready demo metadata')
        ready+=1
        duration=row['duration']
        if type(duration) not in (int,float) or not math.isfinite(duration) or duration<=32:
            raise ValueError('Invalid demo duration')
        audio=local(site,row['audio']);raw=audio.read_bytes()
        if (not re.fullmatch(HASH+r'\.mp3',audio.name) or sha(raw)!=row['audio_sha256']
                or audio.stem!=row['audio_sha256'] or not 1000<len(raw)<95*1024*1024):
            raise ValueError('Demo audio checksum, filename or size differs')
        caption=local(site,row['captions']);data=caption.read_bytes()
        if not re.fullmatch('captions-'+sha(data)+r'\.json',caption.name):
            raise ValueError('Caption checksum differs from immutable filename')
        captions_valid(json.loads(data),duration)
        allowed.update((row['audio'],row['captions']))
    if not ready:
        raise ValueError('Site needs at least one playable complete demo')
    credits=read(site/'demos/credits.json')
    if set(credits)!={'schema','music','usage','verification'} or credits['schema']!=1:
        raise ValueError('Credits schema differs')
    music=credits['music']
    if (set(music)!={'model','creator','model_url','conversion_url','license','license_url'}
            or music['license']!='CC BY-NC 4.0'
            or music['license_url']!='https://creativecommons.org/licenses/by-nc/4.0/'):
        raise ValueError('Music attribution/license is missing')
    if any(not isinstance(credits[k],str) or not credits[k].strip() for k in ('usage','verification')):
        raise ValueError('Usage and review disclosure are required')
    inventory=[]
    for path in sorted(site.rglob('*')):
        safe(path)
        if not path.is_file():
            continue
        name=path.relative_to(site).as_posix()
        if name not in allowed:
            raise ValueError('Unexpected file in deployable site: '+name)
        raw=path.read_bytes()
        if path.suffix=='.png':
            if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Invalid social preview image')
        elif path.suffix!='.mp3':
            text=raw.decode('utf-8')
            if PRIVATE.search(text):
                raise ValueError('Private source path in site: '+name)
        inventory.append({'path':name,'bytes':len(raw),'sha256':sha(raw)})
    if not (site/'index.html').is_file() or not (site/'script.js').is_file() or not (site/'styles.css').is_file():
        raise ValueError('Site HTML, script and stylesheet are required')
    if seo:check_seo(site)
    return {'ready_demos':ready,'files':len(inventory),'bytes':sum(r['bytes'] for r in inventory),
            'inventory_sha256':sha(encoded(inventory))}


def export(book,language,site,duration):
    # This strict local exporter validates source coverage, completion, original
    # captions, MP3 hash, source-bound report and privacy before projecting data.
    import package_public_audiobook_demos as package
    payload,details=package.completed_book(Path(book),language)
    latest=json.loads(payload['latest.json'])
    if not math.isfinite(duration) or abs(duration-(latest['speech_end']+24))>0.1:
        raise ValueError('Duration must match the complete speech plus ending music')
    audio=payload[latest['mp3']];checksum=sha(audio)
    rows=[{k:c[k] for k in CAPTION_FIELDS} for c in latest['captions']]
    captions_valid(rows,duration);caption_bytes=encoded(rows)
    row={k:details[k] for k in ('language','title','author','source')}
    row.update(id=language,status='ready',duration=duration,audio_sha256=checksum,
               audio=f'demos/{language}/{checksum}.mp3',
               captions=f'demos/{language}/captions-{sha(caption_bytes)}.json')
    site=safe(site);catalog_path=site/'demos/catalog.json'
    catalog=read(catalog_path) if catalog_path.exists() else [
        {'id':key,**{k:b[k] for k in ('language','title','author','source')},'status':'preparing'}
        for key,b in package.BOOKS.items()]
    catalog=[row if old['id']==language else old for old in catalog]
    credits={'schema':1,'music':package.MUSIC,
        'usage':'Opening and background music use YuE2-3B by Multimodal Art Projection, '
                'via audio.cpp GGUF. Phrases are mixed with narration, level changes and fades. '
                'These demos include CC BY-NC 4.0 music and are for non-commercial use. Retain attribution when sharing.',
        'verification':'Published demos passed automated source-bound checks. No human listening approval is recorded. '
                       'Original texts are credited at their sources; public-domain status varies by territory.'}
    for relative,raw in ((row['audio'],audio),(row['captions'],caption_bytes)):
        target=safe(site/relative);target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_bytes()!=raw:
            raise ValueError('Never overwrite a differing immutable demo asset')
        if not target.exists():
            target.write_bytes(raw)
    catalog_path.parent.mkdir(parents=True,exist_ok=True)
    catalog_path.write_bytes(encoded(catalog));(site/'demos/credits.json').write_bytes(encoded(credits))
    return {'language':language,'audio_sha256':checksum,'captions':len(rows),'bytes':len(audio)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['export','check'])
    parser.add_argument('--site',type=Path,default=ROOT/'website')
    parser.add_argument('--book',type=Path);parser.add_argument('--language',choices=['en','ru'])
    parser.add_argument('--duration',type=float)
    args=parser.parse_args()
    try:
        if args.command=='export':
            if args.book is None or args.language is None or args.duration is None:
                parser.error('export needs --book, --language and measured --duration')
            result=export(args.book,args.language,args.site,args.duration)
        else:
            result=check(args.site)
        print(json.dumps(result,indent=2))
    except (OSError,ValueError,KeyError,TypeError) as error:
        parser.exit(1,str(error)+'\n')


if __name__=='__main__':
    main()
