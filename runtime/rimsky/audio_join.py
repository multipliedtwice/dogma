"""Conservative audiobook joins: preserve speech, trim/fade silent padding only."""
from __future__ import annotations
import numpy as np

JOIN_POLICY={'version':2,'silence_peak':.0003,'guard_seconds':.04,'fade_seconds':.005,
             'endpoint_peak_limit':.01,'endpoint_rms_limit':.005,'edge_window_seconds':.02,
             'scene_gap':1.15,'heading_gap':.8,'paragraph_gap':.34,'sentence_gap':.24,'part_gap':.12}

def boundary_check(audio,rate,policy=JOIN_POLICY):
    x=np.asarray(audio)
    if x.ndim!=1 or not len(x) or not np.isfinite(x).all():raise ValueError('Invalid mono audio')
    active=np.flatnonzero(np.abs(x)>policy['silence_peak'])
    if not len(active):raise ValueError('Silent take cannot be joined')
    window=max(1,round(rate*policy['edge_window_seconds']))
    head=int(active[0]);tail=len(x)-1-int(active[-1])
    rms=lambda a:float(np.sqrt(np.mean(np.asarray(a,dtype=np.float64)**2)))
    unsafe_start=(abs(float(x[0]))>policy['endpoint_peak_limit'] or (head<window and rms(x[:window])>policy['endpoint_rms_limit']))
    unsafe_end=(abs(float(x[-1]))>policy['endpoint_peak_limit'] or (tail<window and rms(x[-window:])>policy['endpoint_rms_limit']))
    return {'unsafe_start':unsafe_start,'unsafe_end':unsafe_end,'leading_padding':head/rate,
            'trailing_padding':tail/rate,'first_sample':float(x[0]),'last_sample':float(x[-1]),
            'start_rms':rms(x[:window]),'end_rms':rms(x[-window:])}

def prepare_clip(audio,rate,policy=JOIN_POLICY):
    """Never hide a truncated/active edge with a fade or crossfade."""
    check=boundary_check(audio,rate,policy)
    if check['unsafe_start'] or check['unsafe_end']:raise ValueError('Active audio edge requires a retake')
    x=np.asarray(audio);guard=round(policy['guard_seconds']*rate)
    left=max(0,round(check['leading_padding']*rate)-guard)
    right=max(0,round(check['trailing_padding']*rate)-guard)
    y=x[left:len(x)-right if right else len(x)].copy()
    fade=round(policy['fade_seconds']*rate)
    # Assertion makes every transformed sample verifiably quiet. Padding below
    # the threshold is the only permitted region for a taper; no speech overlap.
    faded=[]
    if fade and len(y)>=2*fade:
        if np.max(np.abs(y[:fade]))<=policy['silence_peak']:
            y[:fade]*=np.linspace(0,1,fade);faded.append('start')
        if np.max(np.abs(y[-fade:]))<=policy['silence_peak']:
            y[-fade:]*=np.linspace(1,0,fade);faded.append('end')
    return y,{**check,'trimmed_start_samples':left,'trimmed_end_samples':right,'faded_padding':faded,
              'leading_padding':check['leading_padding']-left/rate,
              'trailing_padding':check['trailing_padding']-right/rate}

def gap_seconds(previous,current,previous_edge,current_edge,policy=JOIN_POLICY):
    if previous is None:return 0
    if previous.get('scene')!=current.get('scene'):target=policy['scene_gap']
    elif previous.get('kind')=='heading':target=policy['heading_gap']
    elif previous['source_id']!=current['source_id']:target=policy['paragraph_gap']
    elif previous.get('sentence_index')!=current.get('sentence_index'):target=policy['sentence_gap']
    else:target=policy['part_gap']
    # Existing model padding is part of the pause, not additional to it.
    return max(0,target-previous_edge['trailing_padding']-current_edge['leading_padding'])
