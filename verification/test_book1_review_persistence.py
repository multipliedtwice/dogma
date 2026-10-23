"""Review edits are tested only in isolated temporary folders."""
import json
import sys
import tempfile
import unittest
import functools
import threading
import urllib.request
import urllib.error
import hashlib
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from book1_review_persistence import save_review, ReviewConflict
from manual_labeling_archive import _mirror


class ReviewPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        (self.folder / 'summary.json').write_text(json.dumps({'candidates_sha256': 'fixture'}))
        (self.folder / 'candidates.jsonl').write_text('\n'.join(json.dumps({'id': cid}) for cid in ['a', 'b']))

    def patch(self, changes):
        return save_review(self.folder, {'schema': 1, 'candidates_sha256': 'fixture', 'changes': changes}, patch=True)

    def test_tabs_edit_different_clips_without_loss(self):
        self.patch({'a': {'before': None, 'after': {'voice_verdict': 'yes'}}})
        result = self.patch({'b': {'before': None, 'after': {'voice_verdict': 'no'}}})
        self.assertEqual(set(result['decisions']), {'a', 'b'})

    def test_independent_fields_merge_and_stale_legacy_is_preserved(self):
        initial = self.patch({'a': {'before': None, 'after': {'voice_verdict': 'yes'}}})['decisions']['a']
        changed = {**initial, 'corrected_mood': 'excited'}
        self.patch({'a': {'before': initial, 'after': changed}})
        result = self.patch({'a': {'before': initial, 'after': {**initial, 'bucket_comment': 'different'}}})
        self.assertEqual(result['decisions']['a']['corrected_mood'], 'excited')
        self.assertEqual(result['decisions']['a']['bucket_comment'], 'different')
        before = (self.folder / 'review-draft.json').read_bytes()
        with self.assertRaises(ReviewConflict):
            save_review(self.folder, {'schema': 1, 'candidates_sha256': 'fixture', 'decisions': {'a': initial}})
        self.assertEqual((self.folder / 'review-draft.json').read_bytes(), before)
        same = {key: value for key, value in result['decisions']['a'].items() if key != '_revision'}
        legacy = save_review(self.folder, {'schema': 1, 'candidates_sha256': 'fixture', 'decisions': {'a': same}})
        self.assertEqual(legacy['decisions']['a'], result['decisions']['a'])

    def test_conflicting_batch_is_atomic(self):
        initial = self.patch({'a': {'before': None, 'after': {'corrected_mood': 'neutral'}}})['decisions']['a']
        self.patch({'a': {'before': initial, 'after': {**initial, 'corrected_mood': 'excited'}}})
        before = (self.folder / 'review-draft.json').read_bytes()
        with self.assertRaises(ReviewConflict):
            self.patch({'a': {'before': initial, 'after': {**initial, 'corrected_mood': 'calm'}},
                        'b': {'before': None, 'after': {'voice_verdict': 'yes'}}})
        self.assertEqual(before, (self.folder / 'review-draft.json').read_bytes())

    def test_unknown_clip_or_hash_rejected(self):
        with self.assertRaises(ValueError):
            self.patch({'unknown': {'before': None, 'after': {}}})
        with self.assertRaises(ValueError):
            save_review(self.folder, {'schema': 1, 'candidates_sha256': 'wrong', 'changes': {}}, patch=True)

    def test_saved_labels_are_mirrored_to_canonical_archive(self):
        board=self.folder/'training-work/character-rb1/fine-tune-all'
        board.mkdir(parents=True)
        (board/'summary.json').write_text(json.dumps({'candidates_sha256':'fixture'}))
        (board/'candidates.jsonl').write_text(json.dumps({'id':'a','book_id':'reality-benders-1'}))
        payload={'schema':1,'candidates_sha256':'fixture','changes':{
            'a':{'before':None,'after':{'corrected_mood':'neutral','bucket_comment':'Original user note'}}}}
        first=save_review(board,payload,patch=True)['decisions']['a']
        original=(board/'review-draft.json').read_bytes()
        book=self.folder/'manual-labeling/reality-benders-1'
        self.assertEqual((book/'review.json').read_bytes(),original)
        save_review(board,{**payload,'changes':{'a':{'before':first,'after':{**first,'corrected_mood':'curious'}}}},patch=True)
        updated=(board/'review-draft.json').read_bytes()
        self.assertEqual((book/'review.json').read_bytes(),updated)
        latest=json.loads((book/'latest.json').read_text())
        self.assertEqual(latest['review_sha256'],hashlib.sha256(updated).hexdigest())
        self.assertFalse((book/'snapshots').exists())
        self.assertEqual(json.loads(updated)['decisions']['a']['bucket_comment'],'Original user note')
        self.assertNotIn('bucket_review',json.loads(updated)['decisions']['a'])
        before_latest=(book/'latest.json').read_bytes()
        with self.assertRaises(ReviewConflict):
            save_review(board,{**payload,'changes':{'a':{'before':first,'after':{**first,'corrected_mood':'excited'}}}},patch=True)
        self.assertEqual((book/'latest.json').read_bytes(),before_latest)

    def test_archive_refuses_older_or_smaller_review(self):
        book=self.folder/'manual-labeling/reality-benders-test'
        first={'schema':1,'candidates_sha256':'fixture','updated_at':'2026-09-30T12:00:00+00:00',
               'decisions':{'a':{'voice_verdict':'yes'},'b':{'mood_verdict':'yes'}}}
        _mirror(book,(json.dumps(first)+'\n').encode())
        before=(book/'latest.json').read_bytes()
        older={**first,'updated_at':'2026-09-30T11:00:00+00:00'}
        with self.assertRaisesRegex(ValueError,'older revision'):_mirror(book,(json.dumps(older)+'\n').encode())
        smaller={**first,'updated_at':'2026-09-30T13:00:00+00:00','decisions':{'a':first['decisions']['a']}}
        with self.assertRaisesRegex(ValueError,'fewer decisions'):_mirror(book,(json.dumps(smaller)+'\n').encode())
        self.assertEqual((book/'latest.json').read_bytes(),before)

    def test_archive_rebinds_only_a_pristine_review(self):
        book=self.folder/'manual-labeling/reality-benders-pristine'
        first={'schema':1,'candidates_sha256':'old-candidates','decisions':{}}
        _mirror(book,(json.dumps(first)+'\n').encode())
        replacement={'schema':1,'candidates_sha256':'new-candidates','decisions':{}}
        _mirror(book,(json.dumps(replacement)+'\n').encode())
        self.assertEqual(json.loads((book/'review.json').read_text())['candidates_sha256'],'new-candidates')

        protected={**replacement,'decisions':{'a':{'corrected_mood':'calm'}}}
        _mirror(book,(json.dumps(protected)+'\n').encode())
        different={**protected,'candidates_sha256':'third-candidates'}
        with self.assertRaisesRegex(ValueError,'different candidate set'):
            _mirror(book,(json.dumps(different)+'\n').encode())

    def test_http_patch_and_conflict_response(self):
        import serve_book1_voice_evidence_atlas as service
        original_root = service.ROOT
        service.ROOT = self.folder
        folder = self.folder / 'training-work/character-rb1/fine-tune-all'
        folder.mkdir(parents=True)
        for name in ['summary.json', 'candidates.jsonl']:
            (folder / name).write_bytes((self.folder / name).read_bytes())
        wave_bytes = bytes(range(256))
        (folder / 'test.wav').write_bytes(wave_bytes)
        server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(service.ReviewHandler, directory=str(self.folder / 'training-work')))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/character-rb1/fine-tune-all/patch-review'
            def send(after):
                data = json.dumps({'schema': 1, 'candidates_sha256': 'fixture',
                                   'changes': {'a': {'before': None, 'after': after}}}).encode()
                request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request) as response:
                    return json.load(response)
            result = send({'corrected_mood': 'neutral'})
            self.assertEqual(result['review']['decisions']['a']['corrected_mood'], 'neutral')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                send({'corrected_mood': 'excited'})
            self.assertEqual(caught.exception.code, 409)
            self.assertEqual(json.load(caught.exception)['conflicts'][0]['fields'], ['corrected_mood'])
            wav_url = f'http://127.0.0.1:{server.server_port}/character-rb1/fine-tune-all/test.wav'
            request = urllib.request.Request(wav_url, headers={'Range': 'bytes=32-63'})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.headers['Accept-Ranges'], 'bytes')
                self.assertEqual(response.headers['Content-Range'], 'bytes 32-63/256')
                self.assertEqual(response.read(), wave_bytes[32:64])
            request = urllib.request.Request(wav_url, headers={'Range': 'bytes=-16'}, method='HEAD')
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.headers['Content-Length'], '16')
                self.assertEqual(response.read(), b'')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(wav_url, headers={'Range': 'bytes=999-'}))
            self.assertEqual(caught.exception.code, 416)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            service.ROOT = original_root


if __name__ == '__main__':
    unittest.main()
