"""Read-only completion report for every requested chapter and its music export."""
import argparse,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import file_digest,write_json
from rimsky.audio_join import boundary_check
from higgs_trial_io import read_json as read


def published_takes(generation,cid,ready,timeline,rows,audio):
    import numpy as np,soundfile as sf
    from rimsky.audio_join import prepare_clip
    from rimsky.util import digest
    from higgs_secondary_verification import apply_secondary,report_paths
    OK=('asr_exact_match','nonverbal_not_spoken_text')
    gains=read(generation/'frozen-gains.json');samples,rate=sf.read(audio,dtype='int16')
    published={r['id']:digest(samples[round(r['start']*rate):round(r['end']*rate)].tobytes()) for r in timeline}
    def pcm(row):
        data,sr=sf.read(row['path'],dtype='float32')
        try:data,_=prepare_clip(data,sr)
        except ValueError:return None  # unsafe-edge takes are never published
        gain=gains.get(row['unit']['speaker']+':'+row['unit']['render_voice'])
        if gain is None or sr!=rate:return None
        return digest(np.clip(np.floor(data*gain*32768),-32768,32767).astype('int16').tobytes())
    by_id={row['unit']['id']:row for row in rows}
    pending={r['id'] for r in timeline if r['id'] not in by_id or by_id[r['id']]['word_check']['status'] not in OK
             or pcm(by_id[r['id']])!=published[r['id']]}
    if not pending:return rows
    found={};unverified={}
    for receipts_path in generation.rglob('receipts.json'):
        if 'diagnosis' in receipts_path.parts:continue
        folder=receipts_path.parent;takes=read(receipts_path).get('takes',{})
        wanted=pending.intersection(takes)-set(found)
        if not wanted or not (folder/'render-plan.json').exists():continue
        plan={u['id']:u for u in read(folder/'render-plan.json')['units']}
        try:
            from regenerate_higgs_book_trial import cached_audit
            audit=cached_audit(folder)
        except (ValueError,KeyError,OSError):continue
        if not audit:continue
        for uid in wanted:
            if uid not in audit['checks'] or uid not in plan:continue
            receipt=takes[uid];row={'unit':plan[uid],'receipt':receipt,'path':str(folder/receipt['audio']),'word_check':audit['checks'][uid]}
            if file_digest(Path(row['path']))!=receipt['audio_sha256'] or pcm(row)!=published[uid]:continue
            # The same audio can sit in several take folders; any copy whose
            # bound evidence verifies it accounts for the published segment.
            # Witness reports live at the take, chunk and generation levels.
            levels=[folder,*[q for q in folder.parents if q.is_relative_to(generation)]]
            paths=list(dict.fromkeys(p for level in levels for p in report_paths(level)))
            checked=apply_secondary({uid:row},paths)[uid]
            if checked['word_check']['status'] in OK:found[uid]=checked
            else:unverified.setdefault(uid,checked)
    found={**unverified,**found}
    return [found.get(r['id'],by_id.get(r['id'])) for r in timeline if r['id'] in found or r['id'] in by_id]


def verify(generation,chapters):
    import soundfile as sf
    from render_higgs_palette_preview import PAGE,MEDIA
    from render_higgs_book_trial import validate_voice_continuity
    state=read(generation/'progress.json');results={}
    for cid in chapters:
        errors=[];ready=state.get('chapters',{}).get(cid,{})
        if not ready.get('complete'):
            results[cid]={'complete':False,'errors':['Chapter is not complete.']};continue
        source=next((generation.parent/'audio-v7'/cid).rglob('render-plan.json'))
        expected=read(source)['units'];timeline=read(generation/cid/(cid+'.timeline.json'))
        if [r['id'] for r in timeline]!=[r['id'] for r in expected]:errors.append('Source unit coverage/order differs.')
        audio=MEDIA/Path(ready['stream']).name
        if file_digest(audio)!=ready['wav_sha256']:errors.append('Published speech hash changed.')
        with sf.SoundFile(audio) as f:
            seconds=len(f)/f.samplerate
            if f.samplerate!=24000 or f.channels!=1 or abs(seconds-ready['seconds'])>.001:errors.append('Speech format or timeline duration differs.')
        selected={}
        # Chunk selections, including nested presentation/number-group sections
        # (which may reuse verified takes from an earlier generation); the
        # most recently written selection of a unit wins.
        paths=[p for p in (generation/cid).glob('chunk-*/**/selected.json')
               if not any(part.startswith(('word-','take-','prior-take')) for part in p.relative_to(generation/cid).parts[1:-1])]
        for path in sorted(paths,key=lambda p:p.stat().st_mtime_ns):selected.update(read(path))
        # Durable listener revisions are part of the final published selection.
        from higgs_reviewed_take_overrides import apply_overrides
        from revise_higgs_spoken_take import apply_revision_overrides
        rows=[selected[r['id']] for r in timeline if r['id'] in selected]
        rows=apply_overrides(generation,cid,rows);rows=apply_revision_overrides(generation,cid,rows)
        # Repairs, nested presentation selections and witness-verified takes all
        # land in the published WAV. Account for each published unit by the take
        # whose processed PCM is exactly in the file, verified through the same
        # witness path publication uses.
        rows=published_takes(generation,cid,ready,timeline,rows,audio)
        unsafe=[];word_flags=[]
        for row in rows:
            unit=row['unit'];receipt=row['receipt'];wav=Path(row['path']);check=row['word_check']
            if file_digest(wav)!=receipt['audio_sha256'] or check['audio_sha256']!=receipt['audio_sha256'] or check['synthesis_key']!=receipt['key']:
                errors.append('Unbound take/audit: '+unit['id']);continue
            data,rate=sf.read(wav,dtype='float32');edge=boundary_check(data,rate)
            if edge['unsafe_start'] or edge['unsafe_end']:unsafe.append(unit['id'])
            if check['status'] not in ('asr_exact_match','nonverbal_not_spoken_text'):word_flags.append(unit['id'])
            plan=unit['synthesis']
            if plan.get('backend')!='nonverbal_asset' and plan['prompt']!=plan['text']:errors.append('Vocal effect entered take: '+unit['id'])
        if len(rows)!=len(timeline):errors.append('Published units missing selected-take evidence.')
        if unsafe:errors.append('Unsafe selected audio edges.')
        if word_flags:errors.append('Unresolved recognition disagreements remain.')
        try:validate_voice_continuity([r['unit'] for r in rows])
        except ValueError as e:errors.append(str(e))
        music_record=generation/cid/(cid+'.music-overlay.json')
        if not music_record.exists():errors.append('Music overlay report missing.')
        else:
            proof=read(music_record)
            if proof['speech_sha256']!=ready['wav_sha256'] or proof['timing_shift']!=0:errors.append('Music altered or shifted speech.')
            if proof.get('combined_peak',1)>=.999:errors.append('Music mix clips.')
            mixed=PAGE/ready.get('audio_with_music','missing.mp3')
            if not mixed.exists() or file_digest(mixed)!=proof['mixed_mp3_sha256']:errors.append('Mixed MP3 missing or changed.')
            else:
                info=json.loads(subprocess.run(['C:/ffmpeg/bin/ffprobe.exe','-v','error','-show_entries','format=duration',
                    '-of','json',str(mixed)],capture_output=True,text=True,check=True).stdout)
                duration=float(info['format']['duration']);tail=proof['music_spec']['tail']
                if abs(duration-seconds-tail)>.15:errors.append('Music export duration/tail mismatch.')
            for overlay in ready.get('music',[]):
                if file_digest(MEDIA/Path(overlay['stream']).name)!=overlay['sha256']:errors.append('Published music hash mismatch.')
        results[cid]={'complete':True,'source_units':len(expected),'published_units':len(timeline),'seconds':seconds,
            'word_flags':word_flags,'unsafe_edges':unsafe,'assignment_flags':ready.get('assignment_flags'),
            'music_layout':ready.get('music_layout'),'errors':errors,
            'listening_ready':not errors,'release_approved':False}
    music_complete=(generation/'scene-music-v2/complete.json').exists()
    result={'generation':generation.name,'requested_chapters':chapters,'chapters':results,
        'music_generation_complete':music_complete,
        'all_chapters_listening_ready':music_complete and all(r.get('listening_ready') for r in results.values()),
        'production_approved':False,'held_sections':state.get('held_sections',{}),
        'limits':'ASR disagreements and assignment flags remain explicit; this report does not manufacture human listening approval.'}
    write_json(generation/'completion-report.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--generation',type=Path,required=True)
    p.add_argument('--chapters',nargs='+',default=['c0001','c0002','c0003']);a=p.parse_args()
    r=verify(a.generation.resolve(),a.chapters)
    print(json.dumps({'all_chapters_listening_ready':r['all_chapters_listening_ready'],
        'chapters':{k:{'complete':v['complete'],'errors':v['errors']} for k,v in r['chapters'].items()}},ensure_ascii=False))
