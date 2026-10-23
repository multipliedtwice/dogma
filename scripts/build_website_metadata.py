"""Maintain localized search/share metadata for the static demo site.

No network, inference or production narration changes. Run after editing pages.
"""
from __future__ import annotations
import argparse
from html import escape
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
BASE='https://multipliedtwice.github.io/dogma/'
PAGES={
    'en':{'path':'index.html','url':BASE,'locale':'en_US',
          'title':'Dogma — Local EPUB reader & AI audiobook narration',
          'description':'Read EPUB and FB2 books locally and hear AI narration with Dogma. '
                        'Listen to a complete English audiobook demo with synchronized text.',
          'image_alt':'Dogma: local reading and AI audiobook narration. English and Russian website.'},
    'ru':{'path':'ru/index.html','url':BASE+'ru/','locale':'ru_RU',
          'title':'Dogma — читалка EPUB и озвучка книг',
          'description':'Dogma — локальная читалка EPUB и FB2 с озвучкой книг. '
                        'Русское демо рассказа Чехова «Хамелеон» готовится. Английский рассказ доступен на английской странице.',
          'image_alt':'Dogma: чтение и озвучка книг. Сайт на русском и английском.'},
}


def metadata(language):
    page=PAGES[language];other=PAGES['ru' if language=='en' else 'en']
    image=BASE+f'assets/social-{language}.png'
    tags=['<!-- seo:start -->', '<meta name="robots" content="index,follow,max-image-preview:large">',
          f'<link rel="canonical" href="{page["url"]}">']
    for code,url in [('en',BASE),('ru',BASE+'ru/'),('x-default',BASE)]:
        tags.append(f'<link rel="alternate" hreflang="{code}" href="{url}">')
    values={'og:type':'website','og:site_name':'Dogma','og:title':page['title'],
            'og:description':page['description'],'og:url':page['url'],'og:locale':page['locale'],
            'og:locale:alternate':other['locale'],'og:image':image,'og:image:type':'image/png',
            'og:image:width':'1200','og:image:height':'630','og:image:alt':page['image_alt']}
    tags.extend(f'<meta property="{key}" content="{escape(value,quote=True)}">' for key,value in values.items())
    twitter={'twitter:card':'summary_large_image','twitter:title':page['title'],
             'twitter:description':page['description'],'twitter:image':image,'twitter:image:alt':page['image_alt']}
    tags.extend(f'<meta name="{key}" content="{escape(value,quote=True)}">' for key,value in twitter.items())
    graph=[{'@type':'WebPage','@id':page['url']+'#webpage','url':page['url'],'name':page['title'],
            'description':page['description'],'inLanguage':language,'isPartOf':{'@id':BASE+'#website'},
            'about':{'@type':'Thing','name':'Dogma'}}]
    if language=='en':
        graph.insert(0,{'@type':'WebSite','@id':BASE+'#website','name':'Dogma','url':BASE,'inLanguage':['en','ru']})
    structured=json.dumps({'@context':'https://schema.org','@graph':graph},ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    tags.extend(['<script type="application/ld+json">'+structured+'</script>','<!-- seo:end -->'])
    return '\n  '.join(tags)


def build(site):
    site=Path(site)
    for language,page in PAGES.items():
        path=site/page['path'];text=path.read_text(encoding='utf-8')
        text=re.sub(r'\s*<!-- seo:start -->.*?<!-- seo:end -->','',text,flags=re.S)
        text,count=re.subn(r'<title>.*?</title>','<title>'+escape(page['title'])+'</title>',text,flags=re.S)
        if count!=1:raise ValueError('Expected one page title')
        text,count=re.subn(r'<meta name="description" content="[^"]*"\s*/?>',
                          '<meta name="description" content="'+escape(page['description'],quote=True)+'">',text)
        if count!=1:raise ValueError('Expected one page description')
        text=text.replace('</head>','  '+metadata(language)+'\n</head>')
        path.write_text(text,encoding='utf-8',newline='\n')
    namespace='http://www.sitemaps.org/schemas/sitemap/0.9';ET.register_namespace('',namespace)
    root=ET.Element('{'+namespace+'}urlset')
    for page in PAGES.values():
        row=ET.SubElement(root,'{'+namespace+'}url');ET.SubElement(row,'{'+namespace+'}loc').text=page['url']
    ET.indent(root,space='  ')
    (site/'sitemap.xml').write_bytes(ET.tostring(root,encoding='utf-8',xml_declaration=True)+b'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--site',type=Path,default=ROOT/'website')
    build(parser.parse_args().site)


if __name__=='__main__':main()
