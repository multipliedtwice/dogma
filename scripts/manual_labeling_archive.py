"""Keep human review files outside experimental training work directories."""
from contextlib import contextmanager
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
from review_label_resolution import resolved_review_mood

ROOT = Path(__file__).resolve().parents[1]

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def atomic_bytes(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    handle,temp=tempfile.mkstemp(prefix='.saving-',dir=path.parent)
    try:
        with os.fdopen(handle,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)

def atomic_json(path,data):
    atomic_bytes(path,(json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))

@contextmanager
def archive_lock(book):
    book.mkdir(parents=True,exist_ok=True)
    with (book/'.archive.lock').open('a+b') as stream:
        stream.seek(0,os.SEEK_END)
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX)
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)

def archive_folder(board):
    board=board.resolve()
    if board.name!='fine-tune-all' or board.parent.parent.name!='training-work':return None
    with (board/'candidates.jsonl').open(encoding='utf-8') as stream:
        first=json.loads(next(line for line in stream if line.strip()))
    book_id=first.get('book_id')
    if not book_id:return None # Tiny isolated persistence fixtures have no book ID.
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*',book_id):raise ValueError('Invalid archive book ID')
    return board.parents[2]/'manual-labeling'/book_id

def _mirror(book,raw):
    review=json.loads(raw)
    current_path=book/'review.json'
    latest_path=book/'latest.json'
    if current_path.is_file() and latest_path.is_file():
        current=json.loads(current_path.read_text(encoding='utf-8'))
        latest=json.loads(latest_path.read_text(encoding='utf-8'))
        if current.get('candidates_sha256')!=review.get('candidates_sha256'):
            # A pristine evaluation board can be rebuilt before the listener
            # makes the first decision. Once either side contains human work,
            # candidate rebinding remains forbidden.
            if current.get('decisions') or review.get('decisions'):
                raise ValueError('Refusing to replace a protected review with a different candidate set')
        if len(review.get('decisions',{})) < len(current.get('decisions',{})):
            raise ValueError('Refusing to regress the protected review to fewer decisions')
        incoming_time=review.get('updated_at')
        protected_time=latest.get('source_updated_at') or current.get('updated_at')
        if incoming_time and protected_time:
            parse=lambda value:datetime.fromisoformat(value.replace('Z','+00:00'))
            if parse(incoming_time) < parse(protected_time):
                raise ValueError('Refusing to replace the protected review with an older revision')
    review_hash=hashlib.sha256(raw).hexdigest()
    now=datetime.now(timezone.utc)
    atomic_bytes(book/'review.json',raw)
    latest={'schema':1,'review_sha256':review_hash,
            'candidates_sha256':review['candidates_sha256'],'decisions':len(review['decisions']),
            'source_updated_at':review.get('updated_at'),'archived_at':now.isoformat()}
    atomic_json(book/'latest.json',latest)
    # Book-specific support manifests may bind directly to the latest raw
    # review without deriving labels. Keep that pointer current on every save.
    # Book 1 intentionally uses support_snapshot_review_sha256 instead and must
    # still be refreshed through archive_book1 after labeling changes.
    manifest_path=book/'archive-manifest.json'
    if manifest_path.is_file():
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        if 'review_sha256' in manifest:
            manifest['review_sha256']=review_hash
            manifest.pop('review_snapshot',None)
            manifest['review_decisions']=latest['decisions']
            atomic_json(manifest_path,manifest)
    return latest

def mirror_review(board,raw):
    """Mirror every durable UI save into the canonical protected review."""
    book=archive_folder(board)
    if book is None:return None
    with archive_lock(book):return _mirror(book,raw)

def archive_book1(root=ROOT):
    board=root/'training-work/character-rb1/fine-tune-all'
    book=root/'manual-labeling/reality-benders-1'
    with archive_lock(book):
        raw=(board/'review-draft.json').read_bytes();review=json.loads(raw)
        candidate_hash=digest(board/'candidates.jsonl')
        if review['candidates_sha256']!=candidate_hash:raise ValueError('Stale review')
        latest=_mirror(book,raw)
        rows={row['id']:row for row in map(json.loads,(board/'candidates.jsonl').read_text(encoding='utf-8').splitlines())}
        if set(review['decisions'])-set(rows):raise ValueError('Unknown reviewed clip IDs')
        precision=json.loads((board/'precision-v2/status.json').read_text(encoding='utf-8'))['clips']
        taxonomy=json.loads((root/'training/voice_families.json').read_text(encoding='utf-8'))
        aliases=taxonomy.get('legacy_aliases',{})
        archive_files=[];audio_index={}
        def preserve(source,target):
            if not source.is_file():return
            raw_source=source.read_bytes();atomic_bytes(target,raw_source)
            archive_files.append({'source':source.relative_to(root).as_posix(),
                'archive':target.relative_to(book).as_posix(),'sha256':hashlib.sha256(raw_source).hexdigest(),
                'bytes':len(raw_source)})
        def audio(path,expected=None):
            source=Path(path.replace('\\','/'))
            source=source if source.is_absolute() else root/source
            source=source.resolve()
            if not source.is_relative_to(root.resolve()) or source.suffix.lower()!='.wav':raise ValueError('Unexpected audio archive source')
            key=source.relative_to(root).as_posix()
            if key in audio_index:return audio_index[key]
            if not source.is_file():raise FileNotFoundError(source)
            sha=digest(source)
            if expected and sha!=expected:raise ValueError(f'Changed audio: {key}')
            target=book/'audio'/f'{sha}.wav'
            if not target.exists():
                target.parent.mkdir(exist_ok=True)
                handle,temp=tempfile.mkstemp(prefix='.audio-',dir=target.parent);os.close(handle)
                try:shutil.copyfile(source,temp);os.replace(temp,target)
                finally:
                    if os.path.exists(temp):os.unlink(temp)
            if digest(target)!=sha:raise ValueError('Corrupt archived audio')
            result={'archive':target.relative_to(book).as_posix(),'sha256':sha,'bytes':target.stat().st_size}
            audio_index[key]=result
            return result
        metadata=['candidates.jsonl','summary.json','quality-screen.json','voice-proposal-overrides.json',
                  'role-voice-proposals.json','paragraph-sources.json','precision-v2/status.json',
                  'precision-v2/repairs.json','precision-v2/clip-asr-audit.jsonl']
        for name in metadata:preserve(board/name,book/'metadata'/name)
        preserve(root/'training/voice_families.json',book/'voice-families.json')
        preserve(root/'scripts/book1_review_shared.js',book/'metadata/review-labels.js')
        preserve(root/'content/reality-benders-1.epub',book/'metadata/original.epub')
        for cid,decision in review['decisions'].items():
            row=rows[cid];p=precision.get(cid,{})
            audio(row['audio'],row.get('audio_sha256'))
            if p.get('audio'):audio(p['audio'],p.get('audio_sha256'))
            if row.get('base_voice_anchor_audio'):audio(row['base_voice_anchor_audio'])
        resolved=[]
        for cid,d in review['decisions'].items():
            row=rows[cid];p=precision.get(cid,{})
            voice=(d.get('confirmed_voice_id') or row.get('base_voice_id_proposed')) if d.get('voice_verdict')=='yes' else d.get('corrected_voice_id') if d.get('voice_verdict')=='no' else None
            mood=resolved_review_mood(', '.join(row.get('mood_proposed') or ['neutral']),
                                      d.get('mood_verdict'),d.get('corrected_mood'))
            chosen=p.get('audio') or row['audio'];key=Path(chosen.replace('\\','/')).as_posix()
            resolved.append({'id':cid,'book_id':row['book_id'],'voice_family':aliases.get(voice,voice),'mood':mood,
                'character_id':row.get('character_id'),'source_id':row['source_id'],'chapter_id':row['chapter_id'],
                'text':row['source_text'],'recording_id':row['recording_id'],'recording_sha256':row['recording_sha256'],
                'text_start':row['text_start'],'text_end':row['text_end'],
                'audio_start':p.get('audio_start',row['audio_start']),'audio_end':p.get('audio_end',row['audio_end']),
                'audio':audio_index[key],'precision_status':p.get('status'),'decision':d,
                'training_acceptance_implied':False,'review_sha256':latest['review_sha256']})
        atomic_bytes(book/'labels.jsonl',''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in resolved).encode('utf-8'))
        # Keep original human exports and older queue reviews as history, not replacements.
        history=[root/'training-work/book1-grouped-review.json',root/'training-work/book1-voice-evidence-review.json']
        history+=list(board.glob('review-draft.before-*.json'))
        for queue in ['fine-tune-candidates','fine-tune-expanded']:
            history.append(board.parent/queue/'review-draft.json')
        atlas=board.parent/'voice-evidence-atlas'
        history+=list((atlas/'reviews').glob('*.json'))
        history+=[atlas/'manifest.json',atlas/'book1-review-import.json']
        history+=list((root/'training-work/stress-rb1').glob('*review*.json'))
        for path in history:
            preserve(path,book/'history'/path.relative_to(root/'training-work'))
        atlas_manifest=json.loads((atlas/'manifest.json').read_text(encoding='utf-8'))
        for case in atlas_manifest['cases']:
            audio(str((atlas/case['source_audio']).relative_to(root)),case['source_audio_sha256'])
        database=root/'training-work/book1-listening-app/library.sqlite3'
        feedback=[]
        if database.is_file():
            with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as db:
                db.row_factory=sqlite3.Row
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='stress_reports'").fetchone():
                    feedback=[dict(row) for row in db.execute('SELECT * FROM stress_reports ORDER BY created,id')]
            atomic_json(book/'feedback/narration-reports.json',{'schema':1,'source':database.relative_to(root).as_posix(),'reports':feedback})
        for path in board.parent.glob('label-predictor-*'):
            if path.is_dir() and (path/'report.json').is_file():
                preserve(path/'label-signoff.json',book/'metadata/label-signoff.json')
                preserve(path/'report.json',book/'metadata/label-predictor-report.json')
        atomic_json(book/'audio-index.json',{'schema':1,'references':audio_index})
        for path in [book/'labels.jsonl',book/'audio-index.json']:
            archive_files.append({'source':'generated from frozen review and verified audio',
                'archive':path.relative_to(book).as_posix(),'sha256':digest(path),'bytes':path.stat().st_size})
        manifest={'schema':1,'book':'reality-benders-1','created_at':datetime.now(timezone.utc).isoformat(),
            'support_snapshot_review_sha256':latest['review_sha256'],'labels':len(resolved),'feedback_reports':len(feedback),
            'audio_files':len({r['archive'] for r in audio_index.values()}),'audio_bytes':sum({r['archive']:r['bytes'] for r in audio_index.values()}.values()),
            'files':archive_files,'audio_index':'audio-index.json',
            'history_scope':'Original human exports, atlas evidence, and older queue reviews; older decisions do not override review.json.',
            'label_scope':'Raw human answers and notes; no inferred listening flags or changed training gates.'}
        atomic_json(book/'archive-manifest.json',manifest)
        # Pick up a concurrent durable save rather than publishing a stale latest pointer.
        current=(board/'review-draft.json').read_bytes()
        if current!=raw:_mirror(book,current)
        return manifest

def verify_archive(book):
    with archive_lock(book):
        latest=json.loads((book/'latest.json').read_text(encoding='utf-8'))
        manifest=json.loads((book/'archive-manifest.json').read_text(encoding='utf-8'))
        def checked(relative,expected):
            target=(book/relative).resolve()
            if not target.is_relative_to(book.resolve()):raise ValueError('Archive path escapes book folder')
            if digest(target)!=expected:raise ValueError(f'Archive checksum failed: {relative}')
        checked('review.json',latest['review_sha256'])
        if latest['review_sha256']!=manifest['support_snapshot_review_sha256']:
            raise ValueError('New manual answers are saved; refresh the complete support archive before using derived labels')
        for item in manifest['files']:checked(item['archive'],item['sha256'])
        index=json.loads((book/manifest['audio_index']).read_text(encoding='utf-8'))['references']
        unique={item['archive']:item for item in index.values()}
        for item in unique.values():checked(item['archive'],item['sha256'])
        raw=json.loads((book/'review.json').read_text(encoding='utf-8'))
        resolved=[json.loads(line) for line in (book/'labels.jsonl').read_text(encoding='utf-8').splitlines()]
        if len(resolved)!=len(raw['decisions']):raise ValueError('Resolved label count differs')
        for row in resolved:
            if row['decision']!=raw['decisions'][row['id']] or row['review_sha256']!=latest['review_sha256']:
                raise ValueError('Resolved label or comment differs from the human answer')
        return {'verified':True,'labels':len(resolved),'support_files':len(manifest['files']),
                'audio_files':len(unique),'feedback_reports':manifest['feedback_reports'],
                'review_sha256':latest['review_sha256']}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify',action='store_true',help='Verify all preserved checksums and exact human answers')
    parser.add_argument('--watch',action='store_true',help='Mirror durable working-review changes every two seconds')
    args=parser.parse_args()
    if args.watch:
        if args.verify:parser.error('Choose watch or verify')
        board=ROOT/'training-work/character-rb1/fine-tune-all'
        book=ROOT/'manual-labeling/reality-benders-1'
        atomic_bytes(book/'.watcher.pid',str(os.getpid()).encode('ascii'))
        print('Manual-label backup watcher active; checking durable review changes every two seconds.',flush=True)
        while True:
            try:
                raw=(board/'review-draft.json').read_bytes()
                current=json.loads((book/'latest.json').read_text(encoding='utf-8'))
                if hashlib.sha256(raw).hexdigest()!=current['review_sha256']:
                    mirror_review(board,raw)
                    print('Saved the canonical manual-label review.',flush=True)
            except (OSError,ValueError) as exc:
                print('Backup retry needed: '+str(exc),flush=True)
            time.sleep(2)
    result=verify_archive(ROOT/'manual-labeling/reality-benders-1') if args.verify else archive_book1()
    print(json.dumps({k:v for k,v in result.items() if k not in ['files']},ensure_ascii=False,indent=2))
