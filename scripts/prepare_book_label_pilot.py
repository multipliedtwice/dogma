"""Resumable, source-bound audiobook ASR/alignment for a small label pilot."""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'training')]
from rimsky_training.inference import repair_zero_duration_words

def clips(out,tracks=('01','02'),book_id='reality-benders-2',clip_prefix='rb2',chapter_offset=3):
    import wave
    from difflib import SequenceMatcher
    from rimsky.books import sentence_spans
    from rimsky.preparation import narratable
    from rimsky_training.pairing import tokens
    from repair_book1_clip_edges import quiet_intervals,boundary
    book=json.loads((out/'source/book.json').read_text(encoding='utf-8'))
    rows=[];held=[]
    for track in tracks:
        chapter_id=f'c{int(track)+chapter_offset:04d}'
        folder=out/'recordings'/track
        contract=json.loads((folder/'contract.json').read_text(encoding='utf-8'))
        source=ROOT/contract['source']
        if sha(source)!=contract['sha256']:raise ValueError('Source changed')
        alignment=json.loads((folder/'align.json').read_text(encoding='utf-8'))
        if alignment['source']['sha256']!=contract['sha256']:raise ValueError('Alignment source changed')
        words=[w for d in alignment['windows'] for w in d['words']]
        audio_tokens=[];word_index=[]
        for i,w in enumerate(words):
            for t,_,_ in tokens(w['text']):audio_tokens.append(t);word_index.append(i)
        chapter=next(c for c in book['chapters'] if c['id']==chapter_id)
        blocks=[b for b in chapter['blocks'] if narratable(b)]
        source_tokens=[];owners=[]
        for b in blocks:
            entries=tokens(b['text']);owners.append((b,entries,len(source_tokens)));source_tokens.extend(t for t,_,_ in entries)
        mapping={}
        for m in SequenceMatcher(None,source_tokens,audio_tokens,autojunk=False).get_matching_blocks():
            if m.size>=2:mapping.update({m.a+j:m.b+j for j in range(m.size)})
        quiet=quiet_intervals(source)
        decoded=folder/'source24.wav'
        if not decoded.exists():subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(source),'-ac','1','-ar','24000','-c:a','pcm_s16le',str(decoded)],check=True)
        target=out/'clips';target.mkdir(exist_ok=True)
        with wave.open(str(decoded),'rb') as wav:
            for block,entries,base in owners:
                for a,b in sentence_spans(block['text']):
                    text=block['text'][a:b];positions=[i for i,(_,l,r) in enumerate(entries) if l>=a and r<=b]
                    identifier=clip_prefix+'_'+hashlib.sha256(f'{block["id"]}:{a}:{b}'.encode()).hexdigest()[:18]
                    aligned=[mapping.get(base+i) for i in positions]
                    reason=None
                    known=[i for i in aligned if i is not None]
                    exact=bool(known and len(known)==len(aligned) and known[-1]-known[0]+1==len(known))
                    coverage=len(known)/len(positions) if positions else 0
                    # ASR often misses a name or one short word. Keep a
                    # separately held review candidate only when both spoken
                    # edges and almost all interior words still align.
                    near=bool(len(positions)>=3 and not exact and coverage>=.8
                        and aligned[0] is not None and aligned[-1] is not None
                        and all(b>a and b-a<=4 for a,b in zip(known,known[1:]))
                        and known[-1]-known[0]+1<=len(positions)+2)
                    if len(positions)<3 or not (exact or near):reason='text_not_exactly_matched'
                    else:
                        start=boundary(quiet,words[word_index[aligned[0]]]['start'])
                        end=boundary(quiet,words[word_index[aligned[-1]]]['end'])
                        if start is None or end is None or not 1.2<=end-start<=30:reason='no_safe_sentence_edges_or_duration'
                    if reason:
                        held.append({'id':identifier,'source_id':block['id'],'text':text,'reason':reason});continue
                    first,last=round(start*24000),round(end*24000)
                    wav.setpos(first);samples=wav.readframes(last-first)
                    path=target/(identifier+'.wav')
                    with wave.open(str(path),'wb') as dest:
                        dest.setnchannels(1);dest.setsampwidth(2);dest.setframerate(24000);dest.writeframes(samples)
                    rows.append({'id':identifier,'book_id':book_id,'chapter_id':chapter_id,'chapter_title':chapter['title'],
                        'recording_id':clip_prefix+'-'+track,'recording_sha256':contract['sha256'],'source_id':block['id'],'source_text':text,
                        'paragraph_text':block['text'],'text_start':a,'text_end':b,'audio_start':first/24000,'audio_end':last/24000,
                        'exact_audio':str(path.relative_to(ROOT)).replace('\\','/'),'exact_audio_sha256':sha(path),
                        'alignment_status':'exact_word_alignment' if exact else 'near_word_alignment_review_only',
                        'word_match_coverage':round(coverage,4),
                        'role':'unresolved','human_reviewed':False,'training_approved':False})
        track_rows=[r for r in rows if r['recording_id']==clip_prefix+'-'+track]
        checkpoint=out/'clips-by-track';checkpoint.mkdir(exist_ok=True)
        (checkpoint/(track+'.jsonl')).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in track_rows),encoding='utf-8')
        save(checkpoint/(track+'-holds.json'),{'clips':[h for h in held if h['source_id'].startswith(chapter_id+'-')],'training_approved':False})
        print(f'Cut track {track}: {len(track_rows)} sentence candidates',flush=True)
    # Aggregate only tracks already cut. This permits resumable whole-book work
    # without changing the immutable Book 2 pilot's candidates or review.
    checkpoint=out/'clips-by-track'
    all_rows=[json.loads(line) for path in sorted(checkpoint.glob('??.jsonl')) for line in path.read_text(encoding='utf-8').splitlines() if line]
    all_holds=[h for path in sorted(checkpoint.glob('??-holds.json')) for h in json.loads(path.read_text(encoding='utf-8'))['clips']]
    (out/'clips.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in all_rows),encoding='utf-8')
    save(out/'alignment-holds.json',{'clips':all_holds,'count':len(all_holds),'training_approved':False})

def audit(out):
    from audit_book1_clip_transcripts import compare
    import torch
    from qwen_asr import Qwen3ASRModel
    rows=[json.loads(l) for l in (out/'clips.jsonl').read_text(encoding='utf-8').splitlines()]
    checkpoint=out/'clip-asr-audit.jsonl'
    completed={r['id']:r for r in map(json.loads,checkpoint.read_text(encoding='utf-8').splitlines())} if checkpoint.exists() else {}
    pending=[r for r in rows if r['id'] not in completed or completed[r['id']]['audio_sha256']!=r['exact_audio_sha256']]
    pending.sort(key=lambda r:r['audio_end']-r['audio_start'])
    model=Qwen3ASRModel.from_pretrained(str(ROOT/'training-work/models/Qwen3-ASR-0.6B'),device_map='cuda:0',dtype=torch.float16,local_files_only=True,max_inference_batch_size=4,max_new_tokens=768)
    with checkpoint.open('a',encoding='utf-8') as stream:
        first=0
        while first<len(pending):
            count=1
            while first+count<len(pending) and count<4 and (count+1)*(pending[first+count]['audio_end']-pending[first+count]['audio_start'])<=48:
                count+=1
            batch=pending[first:first+count]
            for r in batch:
                if sha(ROOT/r['exact_audio'])!=r['exact_audio_sha256']:raise ValueError('Changed cut WAV')
            heard=model.transcribe(audio=[str(ROOT/r['exact_audio']) for r in batch],language='Russian')
            if len(heard)!=len(batch):raise ValueError('Wrong audit batch size')
            for r,h in zip(batch,heard):
                comparison=compare(r['source_text'],h.text)
                reviewed=bool(comparison['ratio']>=.82 and comparison['first_word_match'] and comparison['last_word_match'])
                result={'id':r['id'],'audio_sha256':r['exact_audio_sha256'],'heard':h.text,**comparison,
                        'status':'exact_asr_match' if not comparison['edits'] else 'review_only' if reviewed else 'hold'}
                stream.write(json.dumps(result,ensure_ascii=False)+'\n');stream.flush();completed[r['id']]=result
            first+=count
            print(f'Cut WAV check {first}/{len(pending)}',flush=True)
    usable=[r for r in rows if completed[r['id']]['audio_sha256']==r['exact_audio_sha256'] and completed[r['id']]['status'] in ('exact_asr_match','review_only')]
    save(out/'clip-check-summary.json',{'candidates':len(rows),'exact_matches':sum(completed[r['id']]['status']=='exact_asr_match' for r in usable),
        'review_only':sum(completed[r['id']]['status']=='review_only' for r in usable),'held':len(rows)-len(usable),
        'limit':'Approximate matches are for listening and labeling only; all audio requires independent human review before training.'})
    (out/'checked-clips.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in usable),encoding='utf-8')

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temp.replace(path)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['asr','align','clips','audit'])
    parser.add_argument('--book',default='reality-benders-2')
    parser.add_argument('--book-id',default=None,
                        help='Book identifier stored in candidates; defaults to --book.')
    parser.add_argument('--clip-prefix',default='rb2',
                        help='Stable short prefix used for clip and recording IDs.')
    parser.add_argument('--chapter-offset',type=int,default=3,
                        help='Chapter number = numeric track + this offset.')
    parser.add_argument('--tracks',nargs='+',default=['01','02'])
    parser.add_argument('--out',type=Path,default=ROOT/'training-work/character-rb2/pilot')
    args=parser.parse_args()
    out=args.out.resolve()
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    if args.stage=='clips':
        clips(out,args.tracks,args.book_id or args.book,args.clip_prefix,args.chapter_offset)
        return
    if args.stage=='audit':audit(out);return
    import soundfile as sf
    import torch
    torch.set_num_threads(4)
    recordings=[]
    for track in args.tracks:
        source=ROOT/'content'/args.book/(track+'.mp3')
        folder=out/'recordings'/track;folder.mkdir(parents=True,exist_ok=True)
        contract={'source':str(source.relative_to(ROOT)),'sha256':sha(source),'window_seconds':30}
        path=folder/'contract.json'
        if path.exists() and json.loads(path.read_text(encoding='utf-8'))!=contract:
            raise ValueError('Source contract changed')
        save(path,contract)
        wav=folder/'source.wav'
        if not wav.exists():
            subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(source),'-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)],check=True)
        recordings.append((track,folder,wav,contract))
    if args.stage=='asr':
        from qwen_asr import Qwen3ASRModel
        model=Qwen3ASRModel.from_pretrained(str(ROOT/'training-work/models/Qwen3-ASR-0.6B'),device_map='cuda:0',dtype=torch.float16,local_files_only=True,max_inference_batch_size=4,max_new_tokens=768)
    else:
        from qwen_asr import Qwen3ForcedAligner
        model=Qwen3ForcedAligner.from_pretrained(str(ROOT/'training-work/models/Qwen3-ForcedAligner-0.6B'),device_map='cuda:0',dtype=torch.float16,local_files_only=True)
    for track,folder,wav,contract in recordings:
        directory=folder/args.stage;directory.mkdir(exist_ok=True)
        with sf.SoundFile(wav) as audio:
            count=(audio.frames+audio.samplerate*30-1)//(audio.samplerate*30)
            for first in range(0,count,4):
                pending=[]
                for index in range(first,min(first+4,count)):
                    checkpoint=directory/f'{index:06d}.json'
                    if checkpoint.exists():
                        saved=json.loads(checkpoint.read_text(encoding='utf-8'))
                        if saved['source_sha256']!=contract['sha256']:raise ValueError('Checkpoint source mismatch')
                        continue
                    start=index*30;audio.seek(start*audio.samplerate)
                    samples=audio.read(30*audio.samplerate,dtype='float32')
                    text=None
                    if args.stage=='align':text=json.loads((folder/'asr'/f'{index:06d}.json').read_text(encoding='utf-8'))['text']
                    pending.append((index,start,samples,text,checkpoint))
                if pending:
                    if args.stage=='asr':
                        results=model.transcribe(audio=[(p[2],audio.samplerate) for p in pending],language='Russian',return_time_stamps=False)
                    else:
                        results=model.align(audio=[(p[2],audio.samplerate) for p in pending],text=[p[3] for p in pending],language=['Russian']*len(pending))
                    if len(results)!=len(pending):raise ValueError('Wrong batch size')
                    for p,result in zip(pending,results):
                        index,start,samples,text,checkpoint=p
                        value={'index':index,'start':start,'end':start+len(samples)/audio.samplerate,'source_sha256':contract['sha256'],'human_reviewed':False}
                        if args.stage=='asr':value['text']=result.text
                        else:
                            words=[{'text':i.text,'start':start+float(i.start_time),'end':start+float(i.end_time)} for i in result.items]
                            repair_zero_duration_words(words)
                            if any(w['start']<start or w['end']>value['end']+.01 or w['end']<=w['start'] for w in words):
                                value.update(words=[],rejected_words=words,reason='invalid_alignment')
                            else:value['words']=words
                            value['text']=text
                        save(checkpoint,value)
                print(f'{args.stage} track {track}: {min(first+4,count)}/{count}',flush=True)
        documents=[json.loads(p.read_text(encoding='utf-8')) for p in sorted(directory.glob('*.json'))]
        save(folder/(args.stage+'.json'),{'source':contract,'windows':documents})

if __name__=='__main__':main()
