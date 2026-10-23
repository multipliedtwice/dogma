"""Summarize timed voiced pitch without filling silence or assigning emotion."""
import numpy as np

CONTOUR_SCHEMA = 1


def pitch_contour(times, pitches, speech_start, speech_end, bins=12):
    times, pitches = np.asarray(times), np.asarray(pitches)
    valid = np.isfinite(times) & np.isfinite(pitches) & (pitches > 0)
    times, pitches = times[valid], pitches[valid]
    result = {'contour_schema':CONTOUR_SCHEMA, 'pitch_start_hz':None, 'pitch_end_hz':None,
              'pitch_start_offset_semitones':None, 'pitch_end_offset_semitones':None,
              'pitch_ending_change_semitones':None,
              'relative_pitch_curve_semitones':[None]*bins}
    span = speech_end-speech_start
    if len(pitches)<8 or span<=0:
        return result
    median = float(np.median(pitches))
    relative = 12*np.log2(pitches/median)
    # Real time, not voiced-frame index: gaps remain gaps in the shape.
    edges = np.linspace(speech_start, speech_end, bins+1)
    for index in range(bins):
        mask = (times>=edges[index]) & (times<edges[index+1] if index<bins-1 else times<=edges[index+1])
        if mask.sum()>=3:
            result['relative_pitch_curve_semitones'][index] = round(float(np.median(relative[mask])),3)
    width = min(.85,max(.35,span*.2))
    start = (times>=speech_start) & (times<=speech_start+width)
    ending = (times>=speech_end-width) & (times<=speech_end)
    for name,mask in [('start',start),('end',ending)]:
        if mask.sum()>=4:
            pitch = float(np.median(pitches[mask]))
            result[f'pitch_{name}_hz'] = round(pitch,2)
            result[f'pitch_{name}_offset_semitones'] = round(float(12*np.log2(pitch/median)),3)
    # The last part's rise/fall requires voiced evidence on both sides.
    split = speech_end-width/2
    first_half = ending & (times<=split)
    last_half = ending & (times>split)
    if first_half.sum()>=4 and last_half.sum()>=4:
        result['pitch_ending_change_semitones'] = round(float(
            12*np.log2(np.median(pitches[last_half])/np.median(pitches[first_half]))),3)
    return result
