"""Structural pauses and restrained music bridges, separate from voice synthesis."""
import re

POLICY={'version':2,'scene_seconds':4.0,'chapter_seconds':6.0,
        'lead_seconds':6.0,'speech_gain':1.2,'pause_gain':2.0,'release_seconds':1.2,
        # v2 (user, 2026-10-17): a time-skip or section marker ("За шесть часов до этого…",
        # "A few moments later…") and the chapter title need empty time around them so the
        # listener registers the change; the music bridge swells into these pauses.
        'section_before_seconds':2.5,'section_after_seconds':1.5,
        'title_lead_seconds':1.5,'title_after_seconds':3.0}
MARKER_TAGS=('strong','b','em','i')
MARKER_MAX_CHARACTERS=100


def separator(text):
    return bool(re.fullmatch(r'(?:[＊*]\s*){3,}|⁂',(text or '').strip()))


def section_marker(block,first_heading=False):
    """A structural marker inside a chapter, from the book's own markup: a heading other than
    the chapter title, or a short stand-alone paragraph that is entirely bold or italic and is
    not dialogue. No wording patterns: the typesetting carries the structure."""
    if block.get('kind')=='heading':return not first_heading
    text=(block.get('text') or '').strip()
    full=set((block.get('formatting') or {}).get('full') or [])
    return (block.get('kind')=='paragraph' and 0<len(text)<=MARKER_MAX_CHARACTERS and bool(full & set(MARKER_TAGS))
            and not text.startswith(('—','–','-')))


def structure(blocks,confirmed=()):
    """{source_id: 'title' | 'section'}: the first heading is the title; a section marker counts only
    when rimsky.section_markers confirmed it (markup alone also catches interface readouts)."""
    result={};confirmed=set(confirmed)
    first=next((b['id'] for b in blocks if b.get('kind')=='heading'),None)
    if first:result[first]='title'
    for block in blocks:
        if block['id']!=first and block['id'] in confirmed and section_marker(block):result[block['id']]='section'
    return result


def lead_seconds(first):
    """Silence before the first spoken row of a chapter (music lead-in under a title)."""
    return POLICY['title_lead_seconds'] if first and first.get('structure')=='title' else 0.


def gap_seconds(previous,current,previous_edge,current_edge,policy=None):
    from .audio_join import JOIN_POLICY,gap_seconds as ordinary_gap
    gap=ordinary_gap(previous,current,previous_edge,current_edge,
        {**(policy or JOIN_POLICY),'scene_gap':POLICY['scene_seconds']})
    if previous is None:return gap
    padding=previous_edge['trailing_padding']+current_edge['leading_padding']
    wanted=0.
    if current.get('structure')=='section' and previous.get('source_id')!=current.get('source_id'):
        wanted=POLICY['section_before_seconds']
    if previous.get('structure')=='section' and previous.get('source_id')!=current.get('source_id'):
        wanted=max(wanted,POLICY['section_after_seconds'])
    if previous.get('structure')=='title' and previous.get('source_id')!=current.get('source_id'):
        wanted=max(wanted,POLICY['title_after_seconds'])
    return max(gap,wanted-padding)


def source_boundaries(rows,blocks,confirmed=()):
    positions={b['id']:i for i,b in enumerate(blocks)};marks=structure(blocks,confirmed)
    result=[]
    for before,after in zip(rows,rows[1:]):
        a=positions.get(before.get('source_id'));b=positions.get(after.get('source_id'))
        if a is None or b is None or b<=a:continue
        if any(x.get('kind')=='separator' or separator(x.get('text')) for x in blocks[a+1:b]):
            result.append({'kind':'scene','before':before['id'],'after':after['id']})
        elif marks.get(after.get('source_id'))=='section' or marks.get(before.get('source_id')) in ('section','title'):
            result.append({'kind':'section','before':before['id'],'after':after['id']})
    if rows and marks.get(rows[0].get('source_id'))=='title' and rows[0].get('start',0)>0:
        result.insert(0,{'kind':'title','before':None,'after':rows[0]['id']})
    return result


def windows(rows,boundaries,seconds,chapter_tail=False):
    by_id={r['id']:r for r in rows};result=[]
    for b in boundaries:
        after=by_id[b['after']];start=by_id[b['before']]['end'] if b['before'] else 0.
        result.append({**b,'start':start,'end':after['start']})
    if chapter_tail and rows and seconds>rows[-1]['end']:
        result.append({'kind':'chapter','before':rows[-1]['id'],'start':rows[-1]['end'],'end':seconds})
    return result


def envelope(window,t):
    """A smooth swell, capped under speech; full crest occurs only in the gap."""
    import numpy as np
    start,end=window['start'],window['end'];lead=POLICY['lead_seconds']
    smooth=lambda x: (v:=np.clip(x,0,1))*v*(3-2*v)
    rise=POLICY['speech_gain']*smooth((t-(start-lead))/lead)
    crest=POLICY['speech_gain']+(POLICY['pause_gain']-POLICY['speech_gain'])*smooth((t-start)/.5)
    release=smooth((end-t)/min(POLICY['release_seconds'],max(.1,(end-start)/2)))
    return np.where(t<start,rise,crest)*release


def bridge_layers(layers,scenes,takes,times,transitions):
    """Reuse a verified bed to make each break audible, including pattern gaps."""
    result=[{**layer,'transitions':transitions} for layer in layers]
    ordered=sorted((s for s in scenes if s['anchor_segment_id'] in times),key=lambda s:times[s['anchor_segment_id']])
    for index,window in enumerate(transitions):
        # A chapter's title lead-in precedes its first scene anchor: it opens on that scene's bed.
        active=[s for s in ordered if times[s['anchor_segment_id']]<=window['start']] or ordered[:1]
        if not active:continue
        scene=active[-1]
        take=next((takes[k] for k in sorted(takes) if k.startswith('bed:'+scene['bed']+':')),None)
        if not take:continue
        at=max(0,window['start']-POLICY['lead_seconds'])
        result.append({'key':take['audio_key'],'anchor':scene['anchor_segment_id'],'at':at,
            'duration':take['duration'],'window':None,'kind':'transition','transition':window,'bridge_id':index})
    return result


def segment_windows(segments):
    """Absolute windows for ready production audio; never infer across unknown time."""
    result=[];offset=0.
    for segment in sorted(segments,key=lambda s:s['ordinal']):
        if segment.get('status')!='ready' or not segment.get('duration'):break
        duration=float(segment['duration']);performance=segment.get('performance') or {}
        if separator(segment.get('text')):
            result.append({'kind':'scene','anchor_segment_id':segment['id'],'start':offset,'end':offset+duration})
        pause=performance.get('chapter_transition_pause',0)
        if pause and duration>=pause:
            result.append({'kind':'chapter','anchor_segment_id':segment['id'],'start':offset+duration-pause,'end':offset+duration})
        offset+=duration
    return result
