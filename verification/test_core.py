from __future__ import annotations
import base64
import contextlib
import io
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'runtime'))
from rimsky.analysis import DEGRADED_HOLD, DEGRADED_WARNING, boundary_units, cast_voices, validate_analysis
from rimsky.books import import_book, split_text
from rimsky.export import build_export, captions
from rimsky.jobs import Jobs
from rimsky.packs import load_pack
from rimsky.store import Store
from rimsky.util import AppError, digest, file_digest, inside, write_json
from rimsky.visuals import generate_visual, save_visual
from server import Application, handler_for
from http.server import ThreadingHTTPServer

FB2='''<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info><genre>prose</genre><author><first-name>Synthetic</first-name><last-name>Fixture</last-name></author><book-title>Verification book</book-title><lang>ru</lang></title-info></description><body><section><title><p>Глава первая</p></title><p>Маша открыла книгу. — Читай, — сказал Иван.</p><p>Она посмотрела на формулу: F = ma.</p></section><section><title><p>Глава вторая</p></title><p>— Продолжим? — спросила Маша.</p><p>Иван кивнул.</p></section></body></FictionBook>'''.encode()

def epub_fixture(extra=None):
    entries={'mimetype':b'application/epub+zip','META-INF/container.xml':b'<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>','OEBPS/content.opf':b'<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>EPUB fixture</dc:title><dc:creator>Synthetic Fixture</dc:creator><dc:language>en</dc:language></metadata><manifest><item id="b" href="b.xhtml"/><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>','OEBPS/a.xhtml':b'<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>First in spine</h1><p>Hello <em>world</em>.</p><script>alert(1)</script><math xmlns="http://www.w3.org/1998/Math/MathML"><mi>F</mi><mo>=</mo><mi>m</mi><mi>a</mi></math><table><tr><td>Mass</td><td>2 kg</td></tr></table></body></html>','OEBPS/b.xhtml':b'<html><body><h1>Second in spine</h1><p>Finish.</p></body></html>'}
    entries.update(extra or {})
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,value in entries.items():archive.writestr(name,value)
    return data.getvalue()

def pack_fixture(root):
    root.mkdir(exist_ok=True)
    (root/'model.gguf').write_bytes(b'not-a-real-model; verification only')
    (root/'tts').mkdir(exist_ok=True)
    (root/'tts'/'config.json').write_text('{}')
    pack={'schema':1,'name':'Synthetic verification pack','text_model':{'path':'model.gguf','context':8192},'speech':{'path':'tts','variant':'custom','instruction_control':True},'voices':[{'id':'n','name':'Narrator','role':'narrator','speaker':'Ryan'},{'id':'a','name':'A','speaker':'Aiden','tags':['low']},{'id':'b','name':'B','speaker':'Serena','tags':['bright']}],'files':{'model.gguf':file_digest(root/'model.gguf'),'tts/config.json':file_digest(root/'tts'/'config.json')}}
    write_json(root/'pack.json',pack)
    return load_pack(str(root),verify=True)

def write_wave(path,seconds=.1,rate=24000):
    with wave.open(str(path),'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(rate)
        out.writeframes(b''.join(struct.pack('<h',int(math.sin(2*math.pi*440*i/rate)*1000)) for i in range(round(seconds*rate))))
    return {'duration':seconds,'samples':round(seconds*rate),'sample_rate':rate,'warning':''}

NEUTRAL_CONTEXT={'mood':'neutral','channel':'direct','content_register':'prose','confidence':'high','evidence':[]}

def unit_parts(text,parts):
    units=boundary_units(text)
    return [{'first':[start for start,_ in units].index(part['start']),'last':[end for _,end in units].index(part['end']),**{key:part[key] for key in ('speaker','delivery','evidence')},'context':part.get('context',NEUTRAL_CONTEXT)} for part in parts]

def narration(source):
    return {'id':source['id'],'parts':unit_parts(source['text'],[{'start':0,'end':len(source['text']),'speaker':'narrator','delivery':'neutral','evidence':[]}])}

def no_structure(test_case):
    from unittest.mock import patch
    import worker
    patcher = patch.object(worker, 'local_structure', return_value={})
    patcher.start()
    test_case.addCleanup(patcher.stop)

def agreeing_critic(payload):
    return {'passages':[{'id':target['id'],'reviews':[{'index':part['index'],'role_supported':True,'speaker_supported':True,'alternative':'','evidence':[target['id']],'reason':''}
                                                        for part in target['proposal']]} for target in payload['request']['targets']]}

def complete_audio(store,generation):
    for segment in generation['segments']:
        key=digest(segment['text'])
        path=store.root/'audio'/f'{key}.wav'
        result=write_wave(path)
        result['sha256']=file_digest(path)
        write_json(path.with_suffix('.json'),result)
        store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=? WHERE generation_id=? AND id=?",(key,.1,generation['id'],segment['id']))
    store.execute("UPDATE generations SET status='ready',analysis=? WHERE id=?",(json.dumps({'complete':True}),generation['id']))


def fixture_name_stress(payload):
    """A valid name-stress answer: first vowel of each multi-vowel word, letters unchanged."""
    import re
    def stress(name):
        def word(match):
            w=match[0];vowels=[i for i,c in enumerate(w) if c in 'аеёиоуыэюяАЕЁИОУЫЭЮЯ']
            return w if len(vowels)<2 else w[:vowels[0]+1]+'́'+w[vowels[0]+1:]
        return re.sub(r'[А-Яа-яЁё]+',word,name)
    return {'names':[{'character_id':c['character_id'],'name':c['name'],'stressed':stress(c['name']),
                      'confidence':'low','basis':'fixture'} for c in payload['request']['characters']]}


class SyntheticEngine:
    def __init__(self,fail_after=None,invalid=False):
        self.rendered=0
        self.analyzed=0
        self.analysis_chapters=set()
        self.fail_after=fail_after
        self.invalid=invalid
    def call(self,method,payload,stop):
        if method=='analyze':
            self.analyzed+=1
            self.analysis_chapters.update(s['chapter_id'] for s in payload['segments'])
            if self.invalid:return {'characters':[],'passages':[]}
            return {'characters':[],'passages':[narration(s) for s in payload['segments']]}
        if method=='reconcile':return {'same':False,'evidence':[]}
        if method=='critique':return agreeing_critic(payload)
        if method=='probe':return {'device':payload['device']}
        if method=='performance_direction' and payload.get('kind')=='name_stress':return fixture_name_stress(payload)
        if method=='pronounce':return {'texts':list(payload['texts'])}
        if method=='render':
            if self.fail_after is not None and self.rendered>=self.fail_after:raise AppError('Deliberate interruption in synthetic verification')
            self.rendered+=1
            return write_wave(Path(payload['output']))
        raise AppError('Unexpected verification operation')
    def close(self):pass

class VariedInvalidEngine(SyntheticEngine):
    """Keeps degraded-passage fixtures below the repeated-error circuit breaker."""
    def call(self,method,payload,stop):
        if method=='analyze':
            self.analyzed+=1
            segments=payload['segments']
            self.analysis_chapters.update(s['chapter_id'] for s in segments)
            if len(segments)>1 or segments[0]['ordinal']%2==0:
                return {'characters':[],'passages':[]}
            passage=narration(segments[0])
            return {'characters':[],'passages':[passage,passage]}
        return super().call(method,payload,stop)

class CastEngine(SyntheticEngine):
    def __init__(self):
        super().__init__()
        self.render_voices=set()
    def call(self,method,payload,stop):
        if method=='analyze':
            self.analyzed+=1
            self.analysis_chapters.update(s['chapter_id'] for s in payload['segments'])
            known={c['name']:c['id'] for c in payload['cast']}
            characters=[]
            for name,temporary in [('Маша','new_1'),('Иван','new_2')]:
                evidence=next((s['id'] for s in payload['segments'] if name in s['text']),None)
                if name not in known and evidence:
                    known[name]=temporary
                    characters.append({'id':temporary,'name':name,'aliases':[],'description':'','evidence':[evidence],'kind':'person','gender':{'value':'unknown','evidence':[]},'age_band':{'value':'unknown','evidence':[]}})
            passages=[]
            for segment in payload['segments']:
                text=segment['text']
                parts=[]
                speaker=known.get('Иван') if 'Читай' in text else known.get('Маша') if 'Продолжим?' in text else None
                if speaker:
                    start=text.index('—');end=text.index('—',start+1)
                    if start:parts.append({'start':0,'end':start,'speaker':'narrator','delivery':'neutral','evidence':[]})
                    parts.append({'start':start,'end':end,'speaker':speaker,'delivery':'questioning' if '?' in text[start:end] else 'firm','evidence':[segment['id']]})
                    parts.append({'start':end,'end':len(text),'speaker':'narrator','delivery':'neutral','evidence':[]})
                else:parts=[{'start':0,'end':len(text),'speaker':'narrator','delivery':'neutral','evidence':[]}]
                passages.append({'id':segment['id'],'parts':unit_parts(text,parts)})
            return {'characters':characters,'passages':passages}
        if method=='render':self.render_voices.add(payload['voice_id'])
        return super().call(method,payload,stop)

class AliasEngine(CastEngine):
    def __init__(self,decide=lambda payload:True):
        super().__init__();self.decide=decide;self.reconciled=[]
    def call(self,method,payload,stop):
        if method=='reconcile':
            self.reconciled.append(payload)
            reply=self.decide(payload)
            if isinstance(reply,Exception):raise reply
            first,second=(list(passages) for passages in payload['evidence'].values())
            return {'same':True,'evidence':[first[0],second[0]]} if reply is True else reply
        result=super().call(method,payload,stop)
        if method=='analyze':
            for character in result['characters']:character['aliases']=['Иван']
        return result

class Core(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/'library');self.store.update_settings({'cast_review':False})
        self.book=self.store.import_data(FB2,'fixture.fb2')
    def tearDown(self):
        self.store.close();self.temp.cleanup()
    def generation(self):
        return self.store.create_generation(self.book['id'],{'pack_path':'unused','pack_fingerprint':'fixture','pack_name':'Verification','device':'cpu'})
    def target(self,text='— Да, — сказал Иван.'):
        return {'id':'p-0','text':text,'start':0,'end':len(text),'source_id':'p','ordinal':0,'chapter_id':'c1'}
    def proposal(self,target):
        return {'characters':[],'passages':[narration(target)]}
    def test_fb2_metadata(self):
        self.assertEqual(self.book['title'],'Verification book');self.assertEqual(len(self.book['chapters']),2)
    def test_fb2_cyrillic(self):
        self.assertIn('Маша',self.book['chapters'][0]['blocks'][1]['text'])
    def test_source_unchanged(self):
        self.assertEqual((self.store.root/'books'/self.book['id']/'source.fb2').read_bytes(),FB2)
    def test_import_deduplicated(self):
        self.store.import_data(FB2,'renamed.fb2');self.assertEqual(len(self.store.library()),1)
    def test_epub_spine_order(self):
        book=self.store.import_data(epub_fixture(),'sample.epub');self.assertEqual([c['title'] for c in book['chapters']],['First in spine','Second in spine'])
    def test_math_and_table_preserved(self):
        book=self.store.import_data(epub_fixture(),'sample.epub');kinds=[b['kind'] for b in book['chapters'][0]['blocks']];self.assertIn('equation',kinds);self.assertIn('table',kinds)
    def test_active_content_removed(self):
        book=self.store.import_data(epub_fixture(),'sample.epub');text=json.dumps(book);self.assertNotIn('alert(1)',text);self.assertNotIn('<script',text)
    def test_archive_traversal_rejected(self):
        with self.assertRaises(AppError):self.store.import_data(epub_fixture({'../escape':b'bad'}),'bad.epub')
    def test_archive_absolute_rejected(self):
        with self.assertRaises(AppError):self.store.import_data(epub_fixture({'/escape':b'bad'}),'bad.epub')
    def test_encryption_rejected(self):
        self.assertTrue(self.store.import_data(epub_fixture({'META-INF/encryption.xml':b'<encryption/>'}),'empty-encryption.epub')['chapters'])
        with self.assertRaises(AppError):self.store.import_data(epub_fixture({'META-INF/encryption.xml':b'<encryption><EncryptedData><EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes256-cbc"/><CipherData><CipherReference URI="OEBPS/a.xhtml"/></CipherData></EncryptedData></encryption>'}),'bad.epub')
    def test_external_entities_rejected(self):
        bad=b'<!DOCTYPE FictionBook [<!ENTITY x SYSTEM "file:///etc/passwd">]><FictionBook><body><section><p>&x;</p></section></body></FictionBook>'
        with self.assertRaises(AppError):self.store.import_data(bad,'bad.fb2')
    def test_wrong_format_rejected(self):
        with self.assertRaises(AppError):self.store.import_data(FB2,'fake.pdf')
    def test_segmentation_exact(self):
        text=('Русский текст! Ещё предложение. 🚀\n'*100)+'Конец.';parts=split_text(text);self.assertEqual(''.join(p[2] for p in parts),text);self.assertTrue(all(len(p[2])<=420 for p in parts))
    def test_segmentation_offsets(self):
        text='abc '+('я'*500);self.assertTrue(all(text[a:b]==s for a,b,s in split_text(text)))
    def test_generation_updates_current_audiobook(self):
        first=self.generation();second=self.generation();self.assertEqual(first['id'],second['id']);self.assertEqual(len(self.store.generation_list(self.book['id'])),1)
    def test_chapter_selection(self):
        gid=self.store.create_generation(self.book['id'],{},chapters=[self.book['chapters'][1]['id']]);self.assertTrue(all(s['chapter_id']==self.book['chapters'][1]['id'] for s in gid['segments']))
    def test_unknown_chapter(self):
        with self.assertRaises(AppError):self.store.create_generation(self.book['id'],{},chapters=['missing'])
    def test_audio_saved_per_segment(self):
        generation=self.generation();complete_audio(self.store,generation);self.assertTrue(all(s['audio_hash'] for s in self.store.generation(generation['id'])['segments']))
    def test_revision_reuses_unchanged_audio(self):
        generation=self.generation();complete_audio(self.store,generation);old=self.store.generation(generation['id']);new=self.store.revise(old['id'],[{'id':old['segments'][0]['id'],'spoken':'Новое произношение.'}]);self.assertEqual(new['id'],old['id']);self.assertIsNone(new['segments'][0]['audio_hash']);self.assertEqual(new['segments'][1]['audio_hash'],old['segments'][1]['audio_hash']);self.assertEqual(self.store.playback_generation(old['id'])['segments'][0]['spoken'],old['segments'][0]['spoken'])
    def test_revision_unknown_speaker(self):
        generation=self.generation();complete_audio(self.store,generation)
        with self.assertRaises(AppError):self.store.revise(generation['id'],[{'id':generation['segments'][0]['id'],'speaker':'unlisted'}])
    def test_shared_audio_not_deleted(self):
        generation=self.generation();complete_audio(self.store,generation);shared=self.store.generation(generation['id'])['segments'][1]['audio_hash']
        other=self.store.import_data(FB2.replace(b'Verification book',b'Another book'),'another.fb2')
        current=self.store.create_generation(other['id'],{});complete_audio(self.store,current)
        self.store.delete_generation(generation['id']);self.assertTrue((self.store.root/'audio'/f'{shared}.wav').exists())
    def test_position_persisted(self):
        c=self.book['chapters'][1]['id'];self.store.save_position(self.book['id'],{'chapter_id':c,'offset':1.2});self.assertEqual(self.store.book(self.book['id'])['position']['offset'],1.2)
    def test_position_nonfinite_rejected(self):
        with self.assertRaises(AppError):self.store.save_position(self.book['id'],{'chapter_id':self.book['chapters'][0]['id'],'offset':float('nan')})
    def test_bookmarks_persisted(self):
        source=self.book['chapters'][0]['blocks'][0]['id'];self.store.bookmark(self.book['id'],source,'Review');self.assertEqual(self.store.book(self.book['id'])['bookmarks'][0]['note'],'Review')
    def test_stress_report_carries_exact_synthesis_snapshot_or_null(self):
        generation=self.generation();complete_audio(self.store,generation)
        segments=[s for s in self.store.generation(generation['id'])['segments'] if 'Маша' in s['text']]
        segment=segments[0];snapshot={'prompt':'Всё рассла́блено.','key':'c-u1','take_audio_sha256':'ab'*32,'automatic_pronunciation':{'r':1},'phrase_pronunciation':None}
        plain=self.store.report_stress(generation['id'],segment['id'],segment['source_id'],'Маша',segment['start']+segment['text'].index('Маша'),.05,intended_stress='Ма́ша')
        self.assertIsNone(plain['synthesis_snapshot']);self.assertIsNone(self.store.stress_reports(self.book['id'])[0]['synthesis_snapshot'])
        performance={**segment['performance'],'synthesis_snapshot':snapshot}
        self.store.execute('UPDATE segments SET performance=? WHERE generation_id=? AND id=?',(json.dumps(performance,ensure_ascii=False),generation['id'],segment['id']))
        self.store.report_stress(generation['id'],segment['id'],segment['source_id'],'Маша',segment['start']+segment['text'].index('Маша'),.05,intended_stress='Ма́ша',audio_hash=segment['audio_hash'])
        newest=self.store.stress_reports(self.book['id'])[0]
        self.assertEqual(newest['synthesis_snapshot'],snapshot);self.assertEqual(newest['spoken_text'],segment['spoken'])
    def test_stress_report_keeps_heard_generation_and_audio(self):
        generation=self.generation();complete_audio(self.store,generation)
        segment=next(s for s in self.store.generation(generation['id'])['segments'] if 'Маша' in s['text'])
        report=self.store.report_stress(generation['id'],segment['id'],segment['source_id'],'Маша',segment['start']+segment['text'].index('Маша'),.05,intended_stress='Ма́ша',audio_hash=segment['audio_hash'])
        self.assertEqual(report['audio_hash'],segment['audio_hash'])
        self.assertEqual(report['spoken_text'],segment['spoken'])
        self.assertEqual(report['intended_stress'],'Ма́ша')
        self.assertEqual(self.store.stress_reports(self.book['id'])[0]['selected_text'],'Маша')
        self.store.delete_generation(generation['id'])
        self.assertEqual(self.store.stress_reports(self.book['id'])[0]['generation_id'],generation['id'])
        self.assertFalse((self.store.root/'audio'/f'{report["audio_hash"]}.wav').is_file())
        self.store.collect_audio()
        self.assertEqual(self.store.stress_reports(self.book['id'])[0]['audio_hash'],report['audio_hash'])
    def test_stress_report_rejects_unrelated_text(self):
        generation=self.generation();segment=generation['segments'][0]
        with self.assertRaises(AppError):
            self.store.report_stress(generation['id'],segment['id'],segment['source_id'],'несуществующее слово')
    def test_stress_report_requires_one_marked_vowel_in_the_selected_word(self):
        generation=self.generation();complete_audio(self.store,generation)
        segment=next(s for s in self.store.generation(generation['id'])['segments'] if 'Маша' in s['text'])
        for invalid in (None,'Маша','М́аша','Маша́́','Ма́ше'):
            with self.subTest(invalid=invalid),self.assertRaises(AppError):
                self.store.report_stress(generation['id'],segment['id'],segment['source_id'],'Маша',intended_stress=invalid)
    def test_second_store_reader_does_not_interrupt_live_generation(self):
        generation=self.generation();self.store.status(generation['id'],'rendering')
        observer=Store(self.root/'library')
        try:
            self.assertEqual('rendering',observer.generation(generation['id'])['status'])
            self.assertEqual('rendering',self.store.generation(generation['id'])['status'])
        finally:
            observer.close()
    def test_narration_feedback_keeps_comment_and_category(self):
        generation=self.generation();complete_audio(self.store,generation)
        segment=next(s for s in self.store.generation(generation['id'])['segments'] if 'Маша' in s['text'])
        report=self.store.report_stress(generation['id'],segment['id'],segment['source_id'],'Маша',kind='pacing',comment='Pause before this name.')
        self.assertEqual(('pacing','Pause before this name.'),(report['kind'],report['comment']))
        self.assertEqual(report['comment'],self.store.stress_reports(self.book['id'])[0]['comment'])
        with self.assertRaises(AppError):
            self.store.report_stress(generation['id'],segment['id'],segment['source_id'],'Маша',kind='pacing')
    def test_analysis_exact_coverage(self):
        target=self.target();parts,_=validate_analysis(self.proposal(target),[target],[]);self.assertEqual(''.join(p['text'] for p in parts),target['text'])
    def test_analysis_missing_target_rejected(self):
        target=self.target()
        with self.assertRaises(AppError):validate_analysis({'characters':[],'passages':[]},[target],[])
    def test_analysis_gap_rejected(self):
        target=self.target();proposal=self.proposal(target);proposal['passages'][0]['parts'][0]['first']=1
        with self.assertRaises(AppError):validate_analysis(proposal,[target],[])
    def test_analysis_unknown_speaker_rejected(self):
        target=self.target();proposal=self.proposal(target);proposal['passages'][0]['parts'][0]['speaker']='hallucination'
        with self.assertRaises(AppError):validate_analysis(proposal,[target],[])
    def test_analysis_duplicate_target_rejected(self):
        target=self.target();proposal=self.proposal(target);proposal['passages']*=2
        with self.assertRaises(AppError):validate_analysis(proposal,[target],[])
    def test_analysis_invalid_emotion_rejected(self):
        target=self.target();proposal=self.proposal(target);proposal['passages'][0]['parts'][0]['delivery']='run a shell command'
        with self.assertRaises(AppError):validate_analysis(proposal,[target],[])
    def test_analysis_unicode_codepoints(self):
        target=self.target('🚀 Да.');proposal=self.proposal(target);parts,_=validate_analysis(proposal,[target],[]);self.assertEqual(parts[0]['end'],5)
    def test_cast_deterministic(self):
        chars=[{'id':'a','name':'A'},{'id':'b','name':'B'}];voices=[{'id':'n','role':'narrator'},{'id':'one'},{'id':'two'}];segments=[{'speaker':'a','text':'Hello','chapter_id':'c','source_id':'p1','ordinal':0},{'speaker':'b','text':'Yes','chapter_id':'c','source_id':'p2','ordinal':1}];first=cast_voices(chars,voices,segments);self.assertEqual(first,cast_voices(chars,voices,segments));self.assertNotEqual(first[1]['voice_id'],first[2]['voice_id'])
    def test_unfinished_export_rejected(self):
        generation=self.generation()
        with self.assertRaises(AppError):build_export(self.store,generation['id'],'m4b',None,self.root)
    def test_subtitle_export_uses_exact_durations(self):
        generation=self.generation();complete_audio(self.store,generation);export=build_export(self.store,generation['id'],'vtt',None,self.root);text=(self.store.root/'exports'/export['file']).read_text('utf-8');self.assertTrue(text.startswith('WEBVTT'));self.assertIn('00:00:08.000 --> 00:00:08.100',text)
    def test_srt_export(self):
        generation=self.generation();complete_audio(self.store,generation);export=build_export(self.store,generation['id'],'srt',None,self.root);self.assertIn('00:00:08,000 -->',(self.store.root/'exports'/export['file']).read_text('utf-8'))
    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg not installed')
    def test_m4b_real_encoding(self):
        generation=self.generation();complete_audio(self.store,generation);export=build_export(self.store,generation['id'],'m4b',None,self.root);self.assertGreater(export['size'],128)
        import subprocess
        result=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_chapters','-of','json',str(self.store.root/'exports'/export['file'])]));self.assertEqual(len(result['chapters']),3);self.assertEqual(result['chapters'][0]['tags']['title'],'Dogma');self.assertEqual(float(result['chapters'][1]['start_time']),8.0)
    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg not installed')
    def test_chapter_mp3_zip(self):
        generation=self.generation();complete_audio(self.store,generation);export=build_export(self.store,generation['id'],'chapters',None,self.root)
        with zipfile.ZipFile(self.store.root/'exports'/export['file']) as archive:self.assertEqual(len([n for n in archive.namelist() if n.endswith('.mp3')]),2)
    def test_online_images_default_off(self):
        self.assertFalse(self.store.settings()['online_images'])
        with self.assertRaises(AppError):generate_visual(self.store,self.book['id'],self.book['chapters'][0]['blocks'][0]['id'],'key',True)
    def test_settings_validation(self):
        self.assertEqual(self.store.settings()['character_analysis'], 'auto')
        with self.assertRaises(AppError):self.store.update_settings({'playback_rate':float('inf')})
        self.assertEqual(self.store.update_settings(
            {'speaker_tie_breaker':'codex_cli'})['speaker_tie_breaker'], 'codex_cli')
        self.assertEqual(self.store.update_settings(
            {'character_analysis':'claude_cli',
             'speaker_tie_breaker':'claude_cli'})['character_analysis'],
            'claude_cli')
        with self.assertRaises(AppError):
            self.store.update_settings({'speaker_tie_breaker':'untrusted_cli'})
        self.assertEqual(self.store.settings()['unresolved_policy'],
                         'hold_flagged_segments')
    def test_speaker_correction_resolves_review_record_in_revision(self):
        generation=self.generation();segment=generation['segments'][0]
        analysis={'complete':True,'speaker_resolution':[{
            'target_id':'target','source_id':segment['source_id'],
            'start':segment['start'],'end':segment['end'],'speaker':'unknown',
            'primary_speaker':'unknown','tier':'local',
            'triggers':['primary_unknown'],'status':'review'}]}
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?',
                           (json.dumps(analysis),generation['id']))
        self.store.execute("UPDATE segments SET speaker='unknown',status='review' WHERE generation_id=? AND id=?",
                           (generation['id'],segment['id']))
        revised=self.store.revise(generation['id'],[{'id':segment['id'],
                                  'speaker':'narrator'}])
        record=revised['analysis']['speaker_resolution'][0]
        self.assertEqual((record['speaker'],record['tier'],record['status']),
                         ('narrator','human_correction','resolved'))
        self.assertEqual(next(item for item in revised['segments']
                              if item['id']==segment['id'])['status'],'pending')
    def test_path_boundary(self):
        with self.assertRaises(AppError):inside(self.root,'../escape')
    def test_sealed_pack(self):
        pack=pack_fixture(self.root/'pack');self.assertTrue(pack['_fingerprint'])
    def test_changed_pack_rejected(self):
        pack=pack_fixture(self.root/'pack');(self.root/'pack'/'model.gguf').write_bytes(b'changed')
        with self.assertRaises(AppError):load_pack(str(self.root/'pack'),verify=True)
    def test_extra_pack_file_rejected(self):
        pack_fixture(self.root/'pack');(self.root/'pack'/'new-file').write_text('unexpected')
        with self.assertRaises(AppError):load_pack(str(self.root/'pack'))
    def test_restart_pauses_pending_work(self):
        generation=self.generation();self.store.status(generation['id'],'rendering');self.store.close();self.store=Store(self.root/'library',recover_interrupted=True);self.assertEqual(self.store.generation(generation['id'])['status'],'paused')
    def test_restart_keeps_external_higgs_render_running(self):
        generation=self.generation();config={**generation['config'],'engine':'higgs_audio_tts_q8_external_review'}
        self.store.execute('UPDATE generations SET config=?,status=? WHERE id=?',(json.dumps(config),'rendering',generation['id']))
        self.store.close();self.store=Store(self.root/'library',recover_interrupted=True)
        self.assertEqual(self.store.generation(generation['id'])['status'],'rendering')

    def run_job(self, generation, engine):
        jobs=Jobs(self.store,self.root,engine_factory=lambda _:engine)
        jobs.start(generation['id'])
        jobs.thread.join(timeout=5)
        self.assertFalse(jobs.thread.is_alive())
        return self.store.generation(generation['id'])
    def prepared_generation(self):
        pack=pack_fixture(self.root/'pack')
        return self.store.create_generation(self.book['id'],{'pack_path':pack['_root'],'pack_fingerprint':pack['_fingerprint'],'pack_name':pack['name'],'device':'cpu','align_words':False})
    def test_complete_job_preserves_source(self):
        engine=SyntheticEngine()
        generation=self.run_job(self.prepared_generation(),engine)
        self.assertEqual(generation['status'],'ready',generation['error'])
        self.assertTrue(generation['analysis']['complete'])
        for chapter in self.book['chapters']:
            for block in chapter['blocks']:
                actual=''.join(s['text'] for s in generation['segments'] if s['source_id']==block['id'])
                if block['kind']!='figure':self.assertEqual(actual,block['text'])
        self.assertGreater(engine.rendered,0)
    def test_full_job_spreads_equal_fit_performances_and_preserves_interrupted_prose(self):
        engine=CastEngine()
        ready=self.run_job(self.prepared_generation(),engine)
        self.assertEqual(ready['status'],'ready',ready['error'])
        self.assertEqual({c['name'] for c in ready['cast']},{'Narrator','Маша','Иван'})
        self.assertEqual(len({c['voice_id'] for c in ready['cast']}),3)
        self.assertEqual(len(engine.render_voices),3)
        self.assertEqual(ready['analysis']['casting_warnings'], ['No character voice profile declares gender or age bands; these character traits cannot guide casting.'])
        self.assertIn('questioning',{s['delivery'] for s in ready['segments']})
        for chapter in self.book['chapters']:
            for block in chapter['blocks']:
                self.assertEqual(''.join(s['text'] for s in ready['segments'] if s['source_id']==block['id']),block['text'])
    def test_job_checkpoint_reuses_completed_audio(self):
        initial=self.prepared_generation()
        first=SyntheticEngine(fail_after=2)
        failed=self.run_job(initial,first)
        self.assertEqual(failed['status'],'failed')
        self.assertEqual(sum(s['status']=='ready' for s in failed['segments']),2)
        second=SyntheticEngine()
        ready=self.run_job(failed,second)
        self.assertEqual(ready['status'],'ready',ready['error'])
        self.assertEqual(second.rendered,len(ready['segments'])-2)
        self.assertEqual(second.analyzed,0)
    def test_selected_chapter_still_analyses_whole_book(self):
        original=self.prepared_generation()
        chosen=self.book['chapters'][0]['id']
        generation=self.store.create_generation(self.book['id'],original['config'],chapters=[chosen])
        engine=SyntheticEngine()
        ready=self.run_job(generation,engine)
        self.assertEqual(ready['status'],'ready',ready['error'])
        self.assertEqual(engine.analysis_chapters,{c['id'] for c in self.book['chapters']})
        self.assertEqual({s['chapter_id'] for s in ready['segments']},{chosen})
    def test_job_degrades_targets_still_malformed_after_a_single_reask(self):
        engine=VariedInvalidEngine()
        generation=self.run_job(self.prepared_generation(),engine)
        self.assertEqual(generation['status'],'held',generation['error'])
        self.assertEqual(engine.analyzed,8)
        self.assertEqual(engine.rendered,0)
        self.assertEqual(6,len(generation['analysis']['degraded']))
        self.assertTrue(all(s['speaker']=='unknown' and s['delivery']=='neutral' and s['status']=='review' and DEGRADED_HOLD in s['warning'] for s in generation['segments']))
        self.assertEqual({'degraded'},{item['tier'] for item in generation['analysis']['speaker_resolution']})
    def test_analysis_retry_receives_only_the_rejected_passage(self):
        class RepairEngine(SyntheticEngine):
            def __init__(self):
                super().__init__()
                self.first=True
                self.repairs=[]
            def call(self,method,payload,stop):
                if method=='analyze' and self.first and len(payload['segments'])>1:
                    self.first=False
                    self.analyzed+=1
                    passages=[narration(s) for s in payload['segments']]
                    passages[0]['parts'][0]['speaker']='ghost'
                    self.rejected=passages[0]
                    return {'characters':[],'passages':passages}
                if method=='analyze' and payload.get('retry_error'):
                    self.repairs.append(payload)
                return super().call(method,payload,stop)
        engine=RepairEngine()
        generation=self.run_job(self.prepared_generation(),engine)
        self.assertEqual(generation['status'],'ready',generation['error'])
        self.assertEqual(1,len(engine.repairs))
        repair=engine.repairs[0]
        self.assertEqual(repair['retry_response'],{'passages':[engine.rejected],'characters':[]})
        self.assertIn(f"Passage {engine.rejected['id']}",repair['retry_error'])
        self.assertEqual([s['id'] for s in repair['segments']],[engine.rejected['id']])
    def test_explicit_regeneration_refreshes_audio_in_the_same_output(self):
        old=self.run_job(self.prepared_generation(),SyntheticEngine())
        new=self.store.create_generation(self.book['id'],old['config'])
        engine=SyntheticEngine()
        result=self.run_job(new,engine)
        self.assertEqual(result['status'],'ready',result['error'])
        self.assertEqual(result['id'],old['id']);self.assertGreater(engine.rendered,0)
    def test_revision_regenerates_only_changed_passage(self):
        old=self.run_job(self.prepared_generation(),SyntheticEngine())
        new=self.store.revise(old['id'],[{'id':old['segments'][0]['id'],'spoken':'Отдельная исправленная фраза.'}])
        engine=SyntheticEngine()
        result=self.run_job(new,engine)
        self.assertEqual(result['status'],'ready',result['error'])
        self.assertEqual(engine.analyzed,0)
        self.assertEqual(engine.rendered,1)
    def test_release_check_rejects_source_only(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('release_check',Path(__file__).parents[1]/'scripts'/'check_release.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        issues=module.release_issues(self.root)
        self.assertGreaterEqual(len(issues),6)
    def test_core_binary_layout(self):
        from rimsky.packs import resolve_binary
        (self.root/'infer').mkdir()
        executable=self.root/'infer'/'rimsky-infer'
        executable.write_text('verification placeholder, not executable')
        self.assertEqual(resolve_binary('rimsky-infer',self.root),str(executable))

    def test_authenticated_asset_access(self):
        app=Application(self.root/'service',self.root)
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(app,'a-secret-token'))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url=f'http://127.0.0.1:{server.server_port}/api';body=json.dumps({'op':'library','data':{}}).encode()
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(urllib.request.Request(url,data=body,headers={'Content-Type':'application/json'}))
            request=urllib.request.Request(url,data=body,headers={'Content-Type':'application/json','Authorization':'Bearer a-secret-token'})
            self.assertEqual(json.loads(urllib.request.urlopen(request).read()),[])
            request=urllib.request.Request(url,data=body,headers={'Content-Type':'application/json','Authorization':'Bearer a-secret-token','Origin':'https://malicious.invalid'})
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(request)
        finally:server.shutdown();server.server_close();app.close()

if __name__=='__main__':unittest.main()
