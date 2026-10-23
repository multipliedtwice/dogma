"""Verify the complete Book 2 review board and its protected support archive."""
import hashlib
import gzip
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BOARD=ROOT/'training-work/character-rb2-full/fine-tune-all'
SOURCE=ROOT/'training-work/character-rb2/full'
ARCHIVE=ROOT/'manual-labeling/reality-benders-2-full'


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def main():
    summary=load(BOARD/'summary.json')
    candidates=jsonl(BOARD/'candidates.jsonl')
    ids=[row['id'] for row in candidates]
    assert len(ids)==summary['clips']==2392 and len(ids)==len(set(ids))
    candidate_hash=sha(BOARD/'candidates.jsonl')
    assert candidate_hash==summary['candidates_sha256']

    review=load(BOARD/'review-draft.json')
    latest=load(ARCHIVE/'latest.json')
    manifest=load(ARCHIVE/'archive-manifest.json')
    assert review['candidates_sha256']==candidate_hash==manifest['candidates_sha256']==latest['candidates_sha256']
    assert sha(BOARD/'review-draft.json')==latest['review_sha256']
    assert sha(ARCHIVE/'review.json')==latest['review_sha256']==manifest['review_sha256']
    assert set(review['decisions']).issubset(ids)
    assert len(review['decisions'])==latest['decisions']==manifest['review_decisions']

    migration=load(BOARD/'pilot-label-migration.json')
    transferred={row['new_id'] for row in migration['transferred']}
    assert transferred.issubset(review['decisions']) and len(transferred)==7 and len(migration['held'])==3
    assert all(review['decisions'][cid].get('migrated_from_pilot') for cid in transferred)

    audio_index=load(ARCHIVE/'audio-index.json')
    assert sha(ARCHIVE/'audio-index.json')==manifest['audio_index_sha256']
    assert set(audio_index)==set(ids) and manifest['audio_files']==len(ids)
    by_id={row['id']:row for row in candidates}
    speaker_overrides=load(ARCHIVE/'metadata/speaker-overrides.json')
    assert speaker_overrides['schema']==1
    assert speaker_overrides['candidates_sha256']==candidate_hash
    assert set(speaker_overrides['clips']).issubset(by_id)
    for cid,correction in speaker_overrides['clips'].items():
        assert correction['character_id']
        decision=review['decisions'].get(cid,{})
        assert decision.get('corrected_character_id')==correction['character_id']
        assert decision.get('speaker_verdict') in ('no','corrected')
    for cid,item in audio_index.items():
        row=by_id[cid]
        assert item['sha256']==row['audio_sha256']==row['exact_audio_sha256']
        assert sha(ROOT/row['exact_audio'])==item['sha256']
        assert sha(ARCHIVE/item['archive_path'])==item['sha256']

    for name in ('precision-v2/status.json','quality-screen.json','audio-acoustics.json'):
        assert set(load(BOARD/name)['clips'])==set(ids)
    for name in ('candidates.jsonl','quality-screen.json','audio-acoustics.json',
                 'source-context.json','split-plan.json'):
        assert gzip.decompress((BOARD/(name+'.gz')).read_bytes())==(BOARD/name).read_bytes()
    splits=load(BOARD/'split-plan.json')
    assert splits['candidates_sha256']==candidate_hash and set(splits['clips'])==set(ids)
    suggested=sum(bool(row['suggested']) for row in splits['clips'].values())
    requested=splits['clips']['rb2_af237df714d4c22676']
    assert requested['audio_sha256']==by_id['rb2_af237df714d4c22676']['audio_sha256']
    assert requested['suggested']==[47] and requested['cuts'][0]['offset']==47
    assert requested['cuts'][0]['frame']==88151
    assert requested['cuts'][0]['proposal_method']=='anchored_local_alignment'
    assert requested['cuts'][0]['pause_seconds']==.64
    assert requested['complexity_tags']==['structural_dash_transition']
    assert requested['training_priority']=='high_after_independent_edge_review'
    structural=[row for row in splits['clips'].values()
                if 'structural_dash_transition' in row.get('complexity_tags',[])]
    dash_statuses={name:sum(row.get('structural_dash_review')==name for row in structural)
                   for name in ('word_aligned_split_available','anchored_split_available',
                                'forced_aligned_split_available','grammatical_dash_not_a_delivery_split')}
    assert len(structural)==273
    assert dash_statuses=={'word_aligned_split_available':22,'anchored_split_available':42,
                           'forced_aligned_split_available':24,
                           'grammatical_dash_not_a_delivery_split':185}
    evaluation=splits['evaluation']
    assert evaluation['blocked_exact_match_baseline']['clips']==215
    assert evaluation['blocked_exact_match_baseline']['counts']=={
        'anchored_local_alignment_manual_split_clips':108,
        'anchored_local_alignment_proposal_clips':45,
        'forced_text_alignment_manual_split_clips':43,
        'forced_text_alignment_proposal_clips':21,
        'grammatical_dash_not_a_delivery_split':150,
        'manual_split_available':150,
        'proposal_clips':65}
    for method in ('exact_full_transcript','anchored_local_alignment','forced_text_alignment'):
        actual=sum(any(cut['proposal_method']==method and cut['offset'] in clip['suggested']
                       for cut in clip['cuts']) for clip in splits['clips'].values())
        assert evaluation['counts'].get(method+'_proposal_clips',0)==actual
    assert evaluation['counts']['unresolved_delivery_clips']==0
    assert evaluation['counts']['unresolved_manual_boundary_clips']==0
    assert evaluation['unresolved_for_forced_alignment']==[]
    explanatory=splits['clips']['rb2_8da3c417b01fdb952b']
    assert explanatory['suggested']==[] and explanatory['manual_only_cuts']==[7]
    assert explanatory['cuts'][0]['proposal_method']=='forced_text_alignment'
    placeholder=splits['clips']['rb2_c801dbf042f3505832']
    assert placeholder['suggested']==[56,68,129]
    assert placeholder['voice_prefill_spans']==[
        {'start':56,'end':68,'voice_family_id':'narrator_baseline','kind':'spoken_placeholder'},
        {'start':129,'end':141,'voice_family_id':'narrator_baseline','kind':'spoken_placeholder'}]
    unclear=splits['clips']['rb2_289d8ca780ebd80322']
    assert unclear['suggested']==[26]
    assert next(cut for cut in unclear['cuts'] if cut['offset']==26)['frame']==59152
    assert unclear['voice_prefill_spans']==[
        {'start':26,'end':37,'voice_family_id':'narrator_baseline','kind':'spoken_placeholder'}]
    assert suggested==evaluation['counts']['proposal_clips'] and splits['training_approved'] is False
    suggestions=jsonl(SOURCE/'suggestions-v2.jsonl')
    assert {row['id'] for row in suggestions}==set(ids)
    context=load(SOURCE/'context.json')
    assert context['coverage']=={'source_segments':2835,'analyzed_target_ids':2835,'lanes':3}
    assert len(context['passages'])==3160 and len(context['characters'])==72
    assert len({row['id'] for row in context['passages']})==3160

    comparisons=load(BOARD/'book1-comparisons.json')
    book1_archive=ROOT/'manual-labeling/reality-benders-1'
    book1_latest=load(book1_archive/'latest.json')
    book1_manifest=load(book1_archive/'archive-manifest.json')
    assert comparisons['schema']==1 and comparisons['per_bucket']==4
    assert comparisons['source_review_sha256']==book1_latest['review_sha256']==book1_manifest['support_snapshot_review_sha256']
    comparison_ids=[]
    for bucket,items in comparisons['buckets'].items():
        assert 1<=len(items)<=comparisons['per_bucket']
        for item in items:
            assert bucket==f"{item['voice_family']}|{item['mood']}"
            assert item['id'].startswith('book1_ref_') and item['book_id']=='reality-benders-1'
            assert item['audio'].startswith('manual-labeling/reality-benders-1/audio/')
            assert sha(ROOT/item['audio'])==item['audio_sha256']
            comparison_ids.append(item['id'])
    assert len(comparison_ids)==len(set(comparison_ids))

    audit=jsonl(SOURCE/'clip-asr-audit.jsonl')
    statuses={name:sum(row['status']==name for row in audit) for name in ('exact_asr_match','review_only','hold')}
    assert len(audit)==2816 and statuses=={'exact_asr_match':1197,'review_only':1195,'hold':424}
    assert {row['id'] for row in candidates}=={row['id'] for row in audit if row['status']!='hold'}

    for name,expected in manifest['metadata'].items():
        assert sha(ARCHIVE/'metadata'/name)==expected
    assert sha(ROOT/'content/reality-benders-2.epub')=='4ed8157413044ddc27e1c5a31d4f89b2cda617fde43755e5ec56e4811b133785'
    assert sha(ARCHIVE/'metadata/source.epub')==sha(ROOT/'content/reality-benders-2.epub')

    result={'verified':True,'candidates':len(ids),'audit':statuses,'suggested_splits':suggested,
            'structural_dash_clips':len(structural),'structural_dash_statuses':dash_statuses,
            'review_decisions':len(review['decisions']),'migrated_pilot_decisions':len(transferred),'archive_audio_files':len(audio_index),
            'mixed_mode_buckets':len(comparisons['buckets']),'mixed_mode_references':len(comparison_ids),
            'candidates_sha256':candidate_hash,'review_sha256':latest['review_sha256']}
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
