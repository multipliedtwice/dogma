import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile as sf

sys.path[:0] = [str(Path(__file__).parents[1] / 'runtime'), str(Path(__file__).parent)]
from rimsky import music
from rimsky.export import build_export, mix_music, place_music
from rimsky.opening import select_opening
from rimsky.store import DEFAULTS, Store
from rimsky.util import AppError, digest
from test_core import FB2, complete_audio

SHORT = {kind: {**policy, 'seconds': 2, 'take_seconds': 2, 'fade_in': .2, 'fade_out': .3} for kind, policy in music.CUES.items()}
PACK = {'_fingerprint': 'pack-a', '_root': '.', 'name': 'Fixture', 'generation': {'cot': 'off', 'num_inference_steps': 8},
        'verifier': {'backend': 'qwen3_asr', 'path': 'verifier/asr'}, 'files': {}, 'license': {'attribution': 'Fixture music'}}


def tone(seconds, rate=music.RATE, level=.1):
    t = np.arange(round(seconds * rate)) / rate
    x = level * np.sin(2 * np.pi * 220 * t) * (1 + .3 * np.sin(2 * np.pi * .5 * t))
    return np.stack([x, x], axis=1).astype('float32')


def probe(file):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(file)],
                            capture_output=True, text=True, check=True)
    return float(json.loads(result.stdout)['format']['duration'])


def segment(i, chapter, delivery='neutral', duration=1.0, source=None):
    return {'id': f's{i}', 'ordinal': i, 'chapter_id': chapter, 'delivery': delivery, 'status': 'ready', 'duration': duration,
            'source_id': source, 'performance': {'delivery': delivery}}


BOOK = {'id': 'b', 'title': 'Колония', 'author': 'Автор', 'chapters': [
    {'id': 'c1', 'title': 'Глава 1', 'blocks': [{'id': 'h1', 'kind': 'heading', 'text': 'Глава 1'},
                                                 *[{'id': f'p1-{i}', 'kind': 'paragraph', 'text': f'Шахта и барьер {i}. ' * 60} for i in range(12)]]},
    {'id': 'c2', 'title': 'Глава 2', 'blocks': [{'id': f'p2-{i}', 'kind': 'paragraph', 'text': f'Таверна у ворот {i}. ' * 60} for i in range(12)]}]}
# Each paragraph is ~1.2 minutes of narration, so a chapter is ~14 minutes.
BOOK_SEGMENTS = [segment(i, 'c1', source=f'p1-{i}', duration=70) for i in range(12)] + \
                [segment(12 + i, 'c2', source=f'p2-{i}', duration=70) for i in range(12)]


class Planning(unittest.TestCase):
    def test_each_chapter_gets_a_scene_and_takes_are_shared_by_mood_family(self):
        segments = [segment(0, 'c1'), segment(1, 'c1'), segment(2, 'c2'), segment(3, 'c3', duration=5.0)]
        plan = music.plan_music({'id': 'book'}, {'segments': segments, 'analysis': {}})
        self.assertEqual([(s['id'], s['anchor_segment_id'], s['bed']) for s in plan['scenes']],
                         [('c1:0', 's0', 'calm'), ('c2:0', 's2', 'calm'), ('c3:0', 's3', 'calm')])
        # One family: three variants plus the ending, not one take per chapter.
        self.assertEqual([t['id'] for t in plan['takes']], ['bed:calm:0', 'ending:c3', 'bed:calm:1', 'bed:calm:2'])
        ending = plan['takes'][1]
        self.assertEqual(ending['start'], 5.0 - music.CUES['ending']['accent_after'])
        for take in plan['takes']:
            self.assertTrue(take['style'].startswith('instrumental, ambient, medieval folk') and take['style'].endswith('no vocals'))
            self.assertIn('lute', take['style'])
            self.assertNotIn('piano', take['style'])
        self.assertEqual(len({t['seed'] for t in plan['takes']}), len(plan['takes']))

    def test_mood_comes_from_deliveries_and_a_manual_chapter_mood_wins(self):
        tense = [segment(i, 'c1', 'angry' if i % 3 == 0 else 'neutral') for i in range(12)]
        self.assertEqual(music.plan_music({'id': 'b'}, {'segments': tense, 'analysis': {}})['scenes'][0]['bed'], 'tense')
        quiet = [segment(i, 'c1', 'angry' if i == 0 else 'neutral') for i in range(20)]
        self.assertEqual(music.plan_music({'id': 'b'}, {'segments': quiet, 'analysis': {}})['scenes'][0]['bed'], 'calm')
        directed = music.plan_music({'id': 'b'}, {'segments': tense, 'analysis': {'music_direction': {'chapter:c1': {'mood': 'sad'}}}})
        self.assertEqual((directed['scenes'][0]['bed'], directed['scenes'][0]['mood_source']), ('melancholy', 'direction'))

    def test_every_sound_input_changes_the_render_key(self):
        take = music.plan_music({'id': 'b'}, {'segments': [segment(0, 'c1')], 'analysis': {}})['takes'][0]
        spec = music.cue_spec(PACK, take, 'cpu')
        base = music.render_key(spec, 0)
        variants = [music.render_key(spec, 1), music.render_key(music.cue_spec(PACK, take, 'cuda'), 0),
                    music.render_key(music.cue_spec({**PACK, '_fingerprint': 'pack-b'}, take, 'cpu'), 0),
                    music.render_key(music.cue_spec(PACK, {**take, 'style': take['style'] + ', harp'}, 'cpu'), 0),
                    music.render_key(music.cue_spec(PACK, {**take, 'seed': take['seed'] + 1}, 'cpu'), 0)]
        self.assertEqual(len({base, *variants}), 6)
        ambience = {'kind': 'ambience', 'ambience': 'rain', 'seed': 1}
        self.assertNotEqual(music.render_key(music.cue_spec(PACK, ambience, 'cpu'), 0),
                            music.render_key(music.cue_spec(PACK, {**ambience, 'ambience': 'wind'}, 'cpu'), 0))

    def test_bed_pattern_is_deterministic_and_never_repeats_a_phrase_back_to_back(self):
        pattern = music.bed_pattern(1234, 'rain')
        self.assertEqual(pattern, music.bed_pattern(1234, 'rain'))
        variants = [v for v, _ in pattern['phrases']]
        self.assertTrue(all(a != b for a, b in zip(variants, variants[1:])))
        starts = [at for _, at in pattern['phrases']] + [pattern['cycle']]
        gaps = [b - a - music.CUES['bed']['seconds'] for a, b in zip(starts, starts[1:])]
        self.assertTrue(all(music.GAPS['ambience'][0] <= g <= music.GAPS['ambience'][1] + .01 for g in gaps))
        quiet = music.bed_pattern(1234, 'none')
        self.assertLess(quiet['cycle'], pattern['cycle'])  # Without ambience the gaps are only breaths.


class Direction(unittest.TestCase):
    def answer(self, request, *scenes):
        return {'scenes': [{'start': start, 'mood': mood, 'ambience': ambience} for start, mood, ambience in scenes], 'reason': 'x'}

    def test_style_is_the_house_sound_tinted_by_the_book_palette(self):
        palette = {'genres': ['dark ambient'], 'instruments': ['low strings', 'frame drum']}
        style = music.compose_style('bed', 'tense', palette)
        self.assertTrue(style.startswith('instrumental, ambient, medieval folk, dark ambient, lute, wooden flute, soft hand percussion, '
                                         'low strings, frame drum'))
        self.assertEqual(len(style.split(', ')), len(set(style.split(', '))))
        self.assertTrue(style.endswith('no vocals'))
        vocabulary = {w for v in music.INSTRUMENTS + music.GENRES for w in v.split()}
        self.assertFalse({'choir', 'vocals', 'chant', 'vocalise', 'piano'} & vocabulary)
        self.assertEqual(set(music.BED_OF), set(music.MOOD_WORDS))

    def test_chapter_request_samples_the_whole_chapter_with_minutes(self):
        request = music.chapter_request(BOOK, 'c2', None, {'mood': 'calm', 'ambience': 'none'}, 3000)
        ids = [p['id'] for p in request['passages']]
        self.assertEqual((ids[0], ids[-1]), ('p2-0', 'p2-11'))
        self.assertEqual(len(ids), 10)
        minutes = [p['minute'] for p in request['passages']]
        self.assertEqual(minutes, sorted(minutes))
        self.assertGreater(request['chapter']['minutes'], minutes[-1])
        self.assertLessEqual(sum(len(p['text']) for p in request['passages']), 3000)
        palette = music.palette_request(BOOK, ['c1', 'c2'], 1000)
        self.assertEqual([c['id'] for c in palette['chapters']], ['c1', 'c2'])
        self.assertTrue(palette['chapters'][0]['opening'].startswith('Шахта'))  # Headings are not prose.

    def test_answers_are_validated_against_closed_vocabularies(self):
        request = music.chapter_request(BOOK, 'c1', None, None, 12000)
        self.assertEqual(music.checked_chapter(self.answer(request, ('p1-0', 'ominous', 'cave')), request)['scenes'][0]['mood'], 'ominous')
        for bad in (('p9-0', 'calm', 'none'), ('p1-0', 'neutral', 'none'), ('p1-0', 'calm', 'storm')):
            with self.assertRaises(AppError):
                music.checked_chapter(self.answer(request, bad), request)
        with self.assertRaises(AppError):
            music.checked_chapter({**self.answer(request, ('p1-0', 'calm', 'none')), 'extra': 1}, request)
        with self.assertRaises(AppError):
            music.checked_chapter(self.answer(request, *[('p1-0', 'calm', 'none')] * 4), request)
        with self.assertRaises(AppError):
            music.checked_palette({'genres': ['dark ambient'], 'instruments': ['harp', 'harp'], 'reason': ''})
        with self.assertRaises(AppError):
            music.checked_palette({'genres': [], 'instruments': ['piano'], 'reason': ''})

    def test_only_lasting_turns_change_the_music(self):
        request = music.chapter_request(BOOK, 'c1', None, None, 12000)
        minute = {p['id']: p['minute'] for p in request['passages']}
        checked = lambda *scenes: music.checked_chapter(self.answer(request, *scenes), request)['scenes']
        # The first scene always starts the chapter.
        self.assertEqual(checked(('p1-3', 'calm', 'none'))[0]['start'], 'p1-0')
        # A real turn far enough in is kept.
        turn = next(p for p in minute if minute[p] >= music.MIN_SCENE_MINUTES)
        self.assertEqual([s['mood'] for s in checked(('p1-0', 'calm', 'campfire'), (turn, 'action', 'none'))], ['calm', 'action'])
        # Too soon after the previous scene, too close to the end, or the same bed and ambience: no change.
        self.assertEqual(len(checked(('p1-0', 'calm', 'none'), ('p1-2', 'action', 'none'))), 1)
        self.assertEqual(len(checked(('p1-0', 'calm', 'none'), ('p1-11', 'action', 'none'))), 1)
        self.assertEqual(len(checked(('p1-0', 'calm', 'none'), (turn, 'warm', 'none'), ('p1-11', 'warm', 'none'))), 2)
        self.assertEqual(len(checked(('p1-0', 'hopeful', 'none'), (turn, 'warm', 'none'))), 1)

    def test_scenes_anchor_at_their_passage_and_share_takes(self):
        request = music.chapter_request(BOOK, 'c1', None, None, 12000)
        palette = {'genres': ['dark ambient'], 'instruments': ['low strings']}
        direction = {'palette': palette, 'chapters': {
            'c1': {'scenes': [{'start': 'p1-0', 'mood': 'calm', 'ambience': 'campfire'}, {'start': 'p1-6', 'mood': 'tense', 'ambience': 'none'}]},
            'c2': {'scenes': [{'start': 'p2-0', 'mood': 'eerie', 'ambience': 'cave'}]}}}
        self.assertIn('p1-6', [p['id'] for p in request['passages']])
        plan = music.plan_music(BOOK, {'segments': BOOK_SEGMENTS, 'analysis': {}}, direction)
        self.assertEqual([(s['id'], s['anchor_segment_id'], s['bed'], s['ambience'], s['mood_source']) for s in plan['scenes']],
                         [('c1:0', 's0', 'calm', 'campfire', 'context'), ('c1:1', 's6', 'tense', 'none', 'context'),
                          ('c2:0', 's12', 'mysterious', 'cave', 'context')])
        ids = [t['id'] for t in plan['takes']]
        self.assertEqual(ids[:5], ['ambience:campfire', 'ambience:cave', 'bed:calm:0', 'bed:tense:0', 'bed:mysterious:0'])
        self.assertEqual(len(ids), 2 + 3 * music.BED_VARIANTS + 1)
        self.assertTrue(all('low strings' in t['style'] for t in plan['takes'] if t['kind'] != 'ambience'))
        manual = music.plan_music(BOOK, {'segments': BOOK_SEGMENTS, 'analysis': {'music_direction': {'chapter:c1': {'mood': 'sad'}}}}, direction)
        self.assertEqual([(s['id'], s['bed'], s['ambience']) for s in manual['scenes'][:2]], [('c1:0', 'melancholy', 'campfire'), ('c2:0', 'mysterious', 'cave')])


class Audio(unittest.TestCase):
    def test_finish_trims_beds_to_an_exact_quiet_phrase(self):
        for seconds in (22, 19):
            shaped = music.finish(tone(seconds), 'bed')
            self.assertEqual(len(shaped), music.CUES['bed']['seconds'] * music.RATE)
        body = music.finish(tone(22), 'bed')[3 * music.RATE:15 * music.RATE].mean(axis=1)
        self.assertAlmostEqual(20 * np.log10(np.sqrt(np.mean(body ** 2))), music.CUES['bed']['level_db'], delta=.6)
        self.assertLess(np.abs(shaped[:100]).max(), 1e-3)
        self.assertLess(np.abs(shaped[-100:]).max(), 1e-3)

    def test_ending_rises_after_the_final_words(self):
        shaped = music.finish(tone(24), 'ending').mean(axis=1)
        rms = lambda a, b: np.sqrt(np.mean(shaped[int(a * music.RATE):int(b * music.RATE)] ** 2))
        self.assertGreater(rms(5, 10), rms(.0, 2.0) * 2)

    def test_checks_reject_bad_takes(self):
        with self.assertRaises(AppError):
            music.audio_checks(tone(2)[:, :1], music.RATE, 2)
        with self.assertRaises(AppError):
            music.audio_checks(tone(1), music.RATE, 2)
        gap = tone(10)
        gap[3 * music.RATE:7 * music.RATE] = 0
        with self.assertRaisesRegex(AppError, 'dead-air'):
            music.audio_checks(gap, music.RATE, 10)
        self.assertLess(music.audio_checks(tone(2), music.RATE, 2)['clipped_fraction'], .001)

    def test_ambience_loops_are_seamless_quiet_and_distinct(self):
        loops = {kind: music.synthesize_ambience(kind, 7, seconds=6) for kind in music.AMBIENCES if kind != 'none'}
        for kind, audio in loops.items():
            self.assertEqual(audio.shape, (6 * music.RATE, 2))
            self.assertAlmostEqual(20 * np.log10(np.sqrt(np.mean(audio.mean(axis=1) ** 2))), music.AMBIENCE['level_db'][kind], delta=1.0)
            self.assertLessEqual(np.abs(audio).max(), music.AMBIENCE['peak'])
            # Wrapping from the end to the start is no larger than an ordinary step.
            self.assertLessEqual(np.abs(audio[0] - audio[-1]).max(), np.percentile(np.abs(np.diff(audio, axis=0)), 99.9))
        self.assertTrue(np.array_equal(loops['rain'], music.synthesize_ambience('rain', 7, seconds=6)))
        self.assertFalse(np.array_equal(loops['rain'], music.synthesize_ambience('rain', 8, seconds=6)))
        with self.assertRaises(AppError):
            music.synthesize_ambience('thunder', 7, seconds=1)


class Layout(unittest.TestCase):
    def scenes(self):
        return [{'id': 'a', 'anchor_segment_id': 's0', 'bed': 'calm', 'ambience': 'rain', 'phrases': [[0, 0.0], [1, 30.0]], 'cycle': 60.0},
                {'id': 'b', 'anchor_segment_id': 's2', 'bed': 'tense', 'ambience': 'none', 'phrases': [[1, 0.0]], 'cycle': 25.0}]

    def test_scenes_hand_over_with_a_crossfade(self):
        takes = {'bed:calm:0': {'audio_key': 'c0', 'duration': 20.0}, 'bed:calm:1': {'audio_key': 'c1', 'duration': 20.0},
                 'bed:tense:0': {'audio_key': 't0', 'duration': 20.0}, 'ambience:rain': {'audio_key': 'r', 'duration': 40.0}}
        layers = music.scene_layers(self.scenes(), takes, {'s0': 0.0, 's1': 50.0, 's2': 100.0}, 160.0)
        calm = [(l['key'], l['at']) for l in layers if l['anchor'] == 's0']
        self.assertEqual(calm, [('c0', 0.0), ('c1', 30.0), ('c0', 60.0), ('c1', 90.0), ('r', 0.0), ('r', 40.0), ('r', 80.0)])
        # Only variant 0 of the tense family is ready: the pattern falls back to it.
        self.assertEqual([(l['key'], l['at']) for l in layers if l['anchor'] == 's2'], [('t0', 100.0), ('t0', 125.0), ('t0', 150.0)])
        window = next(l['window'] for l in layers if l['anchor'] == 's0')
        self.assertEqual(window, [0.0, 100.0, music.CROSSFADE])
        gain = music.window_gain(window, np.array([0.0, 3.0, 50.0, 100.0, 103.0, 106.0]))
        self.assertTrue(np.allclose(gain, [0, .5, 1, 1, .5, 0]))
        ending = {'anchor_segment_id': 's2', 'start': 40.0}
        last = music.scene_layers(self.scenes(), takes, {'s0': 0.0, 's2': 100.0}, 160.0, ending)
        self.assertEqual(next(l['window'] for l in last if l['anchor'] == 's2'), [100.0, 140.0, music.CUES['ending']['accent_after']])

    def test_export_track_follows_windows_and_stops_with_speech(self):
        with tempfile.TemporaryDirectory() as folder:
            bed = Path(folder) / 'bed.wav'
            sf.write(bed, tone(4, level=.2), music.RATE, subtype='PCM_16')
            items = [{'id': 'a', 'samples': 24000 * 4, 'sample_rate': 24000}, {'id': 'b', 'samples': 24000 * 4, 'sample_rate': 24000}]
            overlays = [{'path': str(bed), 'anchor_segment_id': 'a', 'start': 0.0, 'duration': 4.0, 'window': [0.0, 4.0, 2.0], 'kind': 'bed', 'gain': .5},
                        {'path': str(bed), 'anchor_segment_id': 'b', 'start': 2.0, 'duration': 4.0, 'window': None, 'kind': 'ending', 'gain': .5},
                        {'path': str(bed), 'anchor_segment_id': 'x', 'start': 0.0, 'duration': 4.0, 'window': None, 'kind': 'ending', 'gain': 1}]
            placed, tail, speech = place_music(items, overlays)
            self.assertEqual([p['at'] for p in placed], [0.0, 6.0])
            self.assertEqual((tail, speech), (2.0, 8.0))
            track = Path(folder) / 'track.wav'
            mix_music(placed, speech, tail, track)
            audio, rate = sf.read(track)
            self.assertEqual((rate, len(audio)), (24000, 24000 * 10))
            rms = lambda a, b: np.sqrt(np.mean(audio[int(a * rate):int(b * rate)] ** 2))
            self.assertLess(rms(0, .2), rms(1.8, 2.2) / 4)  # Fades in over the crossfade.
            self.assertGreater(rms(9, 9.5), 0)  # Ending music runs past the speech.
            self.assertLess(rms(5, 5.9), 1e-4)  # Between the scene and the ending: silence.


class Jobs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'library')
        self.store.update_settings({'cast_review': False})
        book = self.store.import_data(FB2, 'fixture.fb2')
        self.generation = self.store.create_generation(book['id'], {'pack_path': 'unused', 'pack_fingerprint': 'fixture', 'pack_name': 'Fixture', 'device': 'cpu'})
        complete_audio(self.store, self.generation)
        self.speech = SimpleNamespace(active=None)
        self.heard = []
        self.generated = 0
        for patcher in (patch.dict(music.CUES, SHORT), patch.dict(music.AMBIENCE, {'seconds': 3})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.music = music.MusicJobs(self.store, self.speech, self.root / 'resources', start=False,
                                     pack_loader=lambda path, verified: PACK, generator=self.generate,
                                     vocal_check=lambda pack, path: list(self.heard), device_resolver=lambda pack, device: 'cpu')
        self.music.pack_path = lambda: self.root / 'pack'

    def tearDown(self):
        self.music.shutdown()
        self.store.close()
        self.temp.cleanup()

    def generate(self, pack, spec, take, output, stop):
        self.generated += 1
        sf.write(output, tone(spec['request']['semantic_max_tokens'] / music.SEMANTIC_RATE), music.RATE, subtype='PCM_16')
        return output

    def cues(self):
        return self.music.status(self.generation['id'])['cues']

    def test_defaults_and_validation(self):
        self.assertIs(DEFAULTS['music'], True)
        self.assertEqual(DEFAULTS['music_volume'], 1.0)
        for bad in ({'music': 'yes'}, {'music_volume': 1.5}, {'music_volume': True}, {'music_pack_path': 3}):
            with self.assertRaises(AppError):
                self.store.update_settings(bad)

    def test_generates_verified_takes_and_serves_only_ready_audio(self):
        self.music.tick()
        status = self.music.status(self.generation['id'])
        self.assertTrue(status['cues'] and all(c['status'] == 'ready' for c in status['cues']))
        self.assertEqual([s['chapter_id'] for s in status['scenes']], list(dict.fromkeys(
            s['chapter_id'] for s in self.store.generation(self.generation['id'])['segments'])))
        self.assertTrue(all(s['released'] and s['phrases'] and s['cycle'] > 0 for s in status['scenes']))
        with self.music.open(status['cues'][0]['audio_key']) as source:
            self.assertEqual(source.read(4), b'RIFF')
        with self.assertRaises(AppError):
            with self.music.open(digest('unknown')):
                pass
        before = self.generated
        self.music.tick()  # Unchanged plan: no new takes.
        self.assertEqual(self.generated, before)

    def test_music_off_or_narration_running_generates_nothing(self):
        self.store.update_settings({'music': False})
        self.music.tick()
        self.assertEqual((self.generated, self.cues()), (0, []))
        self.store.update_settings({'music': True})
        self.speech.active = 'another-generation'
        self.music.tick()
        self.assertEqual(self.generated, 0)

    def test_audible_words_fail_after_retakes_and_retry_resets(self):
        self.heard = ['la', 'la']
        self.music.tick()
        cues = self.cues()
        self.assertTrue(all(c['status'] == 'failed' and 'audible words' in c['error'] for c in cues))
        self.assertEqual(self.generated, music.TAKES * len(cues))
        self.heard = []
        self.music.retry(self.generation['id'])
        self.music.tick()
        self.assertTrue(all(c['status'] == 'ready' for c in self.cues()))

    def test_export_mixes_music_without_moving_speech_or_skips_it_when_off(self):
        self.music.tick()
        gid = self.generation['id']
        speech = sum(s['duration'] for s in self.store.generation(gid)['segments'])
        opening = select_opening(self.store.generation(gid))['duration']
        with_music = build_export(self.store, gid, 'mp3', None, self.root, music=lambda segments: self.music.export_overlays(gid, segments))
        overlays = self.music.export_overlays(gid, self.store.generation(gid)['segments'])
        self.assertTrue(overlays and all(o['gain'] == DEFAULTS['music_volume'] for o in overlays))
        self.assertEqual({o['kind'] for o in overlays}, {'bed', 'ending'})
        self.assertGreater(probe(self.store.root / 'exports' / with_music['file']), opening + speech + 1)
        self.store.update_settings({'music': False})
        without = build_export(self.store, gid, 'mp3', None, self.root, music=lambda segments: self.music.export_overlays(gid, segments))
        self.assertAlmostEqual(probe(self.store.root / 'exports' / without['file']), opening + speech, delta=.15)

    def test_provider_direction_is_cached_and_failures_fall_back(self):
        asked = []

        def director(payload, generation):
            asked.append(payload)
            if payload['phase'] == 'palette':
                return {'genres': ['dark ambient'], 'instruments': ['low strings', 'frame drum'], 'reason': 'colony'}
            if self.failing:
                raise AppError('provider offline')
            first = payload['request']['passages'][0]['id']
            return {'scenes': [{'start': first, 'mood': 'tense', 'ambience': 'rain'}], 'reason': 'mine collapse'}

        self.failing = True
        self.music.director = director
        self.music.tick()
        status = self.music.status(self.generation['id'])
        self.assertTrue(status['cues'] and all(c['status'] == 'ready' for c in status['cues']))
        self.assertTrue(all('low strings' in c['style'] for c in status['cues']))
        self.assertTrue(all(s['ambience'] == 'none' and s['mood_source'] == 'deliveries' for s in status['scenes']))
        self.assertTrue(all(p['analysis_mode'] == 'local' for p in asked))
        calls = len(asked)
        self.music.tick()
        self.assertEqual(len(asked), calls)  # Answers and failures are remembered.
        self.failing = False
        self.music.retry(self.generation['id'])
        self.music.tick()
        status = self.music.status(self.generation['id'])
        self.assertTrue(all(s['bed'] == 'tense' and s['ambience'] == 'rain' for s in status['scenes']))
        self.assertIn('ambience:rain', [c['id'] for c in status['cues'] if c['status'] == 'ready'])
        self.assertEqual(status['directed'], len(status['scenes']))
        before = (len(asked), self.generated)
        self.music.directions.clear()
        self.music.tick()  # A restart reuses stored answers and audio.
        self.assertEqual((len(asked), self.generated), before)

    def test_external_generation_without_local_pack_uses_direction_fallback(self):
        self.music.engine_factory = lambda: self.fail('engine must not start without a pinned pack')
        self.music.jobs = SimpleNamespace(active=None, _pack=lambda config: config['pack_path'])
        with self.assertRaisesRegex(AppError, 'no pinned local music-direction model'):
            self.music._ask({'analysis_mode': 'local'}, {'config': {}})

    def test_direction_is_not_requested_while_narration_runs(self):
        self.speech.active = 'another-generation'
        self.music.director = lambda payload, generation: self.fail('provider asked while blocked')
        self.music.tick()
        self.assertEqual(self.cues(), [])


if __name__ == '__main__':
    unittest.main()
