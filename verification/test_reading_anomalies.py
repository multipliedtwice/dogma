import sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT/'runtime'))
from rimsky import reading_anomalies as ra

SHIP = 'За все время на «Атоме — 5» не было активности. Позже «Атом-5» ожил.'
BLOCKS = [{'id': 'b1', 'text': SHIP},
          {'id': 'b2', 'text': 'Он пришёл с прось- бой о помощи.'},
          {'id': 'b3', 'text': '— Я вернусь — сказал он — завтра.'},
          {'id': 'b4', 'text': 'Атом — 5 и Атом — 5.'}]
spoken_of = lambda t: ' '.join(t.replace('«', '').replace('»', '').replace('5', 'пять').split()).lstrip('— ')


def item(block_id, original, replacement, kind='dash_as_hyphen', rationale='r'):
    return {'block_id': block_id, 'original': original, 'replacement': replacement, 'kind': kind, 'rationale': rationale}


def check(*items):
    return ra.validate({'repairs': list(items)}, BLOCKS)


class ValidateTests(unittest.TestCase):
    def test_spaced_dash_designation_is_accepted_with_span(self):
        ok, bad = check(item('b1', 'Атоме — 5', 'Атоме-5'))
        self.assertEqual((len(ok), bad), (1, []))
        self.assertEqual(SHIP[ok[0]['start']:ok[0]['end']], 'Атоме — 5')

    def test_hyphenation_break_is_accepted(self):
        ok, bad = check(item('b2', 'прось- бой', 'просьбой', 'hyphenation_break'))
        self.assertEqual((len(ok), bad), (1, []))

    def test_any_letter_or_digit_change_is_rejected(self):
        for new in ('Атомэ-5', 'Атоме-6', 'атоме-5', 'Атоме-пять', 'Атоме'):
            ok, bad = check(item('b1', 'Атоме — 5', new))
            self.assertEqual(ok, [], new); self.assertIn('letters or digits', bad[0]['reason'])

    def test_unknown_kind_and_block_are_rejected(self):
        ok, bad = check(item('b1', 'Атоме — 5', 'Атоме-5', 'rewrite_sentence'), item('zz', 'a', 'b'))
        self.assertEqual(ok, []); self.assertEqual([b['reason'] for b in bad], ['unknown kind', 'unknown block'])

    def test_non_unique_or_absent_original_is_rejected(self):
        ok, bad = check(item('b4', 'Атом — 5', 'Атом-5'), item('b1', 'нет такого', 'нет-такого'))
        self.assertEqual(ok, []); self.assertTrue(all('exactly once' in b['reason'] for b in bad))

    def test_unchanged_and_overlapping_are_rejected(self):
        ok, bad = check(item('b1', 'Атоме — 5', 'Атоме — 5'), item('b1', 'Атоме — 5', 'Атоме-5'),
                        item('b1', 'на «Атоме — 5»', 'на «Атоме-5»'))
        self.assertEqual(len(ok), 1); self.assertIn('empty or unchanged', bad[0]['reason'])
        self.assertIn('overlaps', bad[1]['reason'])

    def test_leading_dialogue_dash_is_protected(self):
        # Mid-sentence structural dashes are kept out by the prompt and kind enum; the leading speech dash by code.
        ok, bad = check(item('b3', '— Я вернусь', 'Я вернусь', 'false_pause_punctuation'))
        self.assertEqual(ok, []); self.assertIn('leading dialogue dash', bad[0]['reason'])

    def test_structural_dash_proposal_with_letter_change_is_rejected(self):
        ok, bad = check(item('b3', 'вернусь — сказал', 'вернусь сказал он'))
        self.assertEqual(ok, []); self.assertIn('letters or digits', bad[0]['reason'])

    def test_malformed_container_raises(self):
        with self.assertRaises(ValueError):ra.validate({}, BLOCKS)
        ok, bad = ra.validate({'repairs': ['x', {'block_id': 1}]}, BLOCKS)
        self.assertEqual((ok, len(bad)), ([], 2))

    def test_schema_is_closed_and_bounded(self):
        s = ra.schema(['b1'])['properties']['repairs']
        self.assertEqual(s['items']['properties']['kind']['enum'], list(ra.KINDS))
        self.assertFalse(s['items']['additionalProperties']); self.assertEqual(s['items']['properties']['block_id']['enum'], ['b1'])

    def test_request_carries_spoken_text_and_is_hashable(self):
        req = ra.request(BLOCKS + [{'id': 'e', 'text': '  '}], spoken_of)
        self.assertEqual([b['id'] for b in req['blocks']], ['b1', 'b2', 'b3', 'b4'])
        self.assertIn('Атоме — пять', req['blocks'][0]['spoken'])
        self.assertEqual(ra.request_sha256(req), ra.request_sha256(ra.request(BLOCKS, spoken_of)))
        self.assertIn('NEVER anomalies', ra.SYSTEM)


class CorrectionTests(unittest.TestCase):
    EVIDENCE = {'path': 'a.json', 'sha256': 'x', 'request_sha256': 'r', 'answer_sha256': 'x', 'provider': 'codex_cli', 'model': 'gpt-6-luna'}

    def units(self):
        text = SHIP; cut = text.index('. ') + 2
        return [{'id': 'c1-u1', 'source_id': 'b1', 'start': 0, 'end': cut, 'speaker': 'narrator', 'text': text[:cut]},
                {'id': 'c1-u2', 'source_id': 'b1', 'start': cut, 'end': len(text), 'speaker': 'narrator', 'text': text[cut:]}]

    def test_mapping_onto_unit_keeps_source_fields_and_audit_target(self):
        ok, _ = check(item('b1', 'Атоме — 5', 'Атоме-5'))
        out, unmapped = ra.to_corrections(ra.to_records(ok, 'c1', self.EVIDENCE), self.units(), spoken_of)
        self.assertEqual((list(out), unmapped), (['c1-u1'], []))
        edit = out['c1-u1']; unit = self.units()[0]
        self.assertTrue(all(edit[k] == unit[k] for k in ('source_id', 'start', 'end', 'speaker', 'text')))
        self.assertIn('Атоме — пять', edit['expected_spoken']); self.assertIn('Атоме-пять', edit['native_text'])
        self.assertEqual(edit['scope'], ra.SCOPE); self.assertEqual(edit['evidence']['model'], 'gpt-6-luna')
        ra.check_spoken_edit(edit['expected_spoken'], edit['native_text'])

    def test_span_across_units_is_reported_unmapped(self):
        ok, _ = check(item('b1', 'Атоме — 5', 'Атоме-5'))
        r = ra.to_records(ok, 'c1', self.EVIDENCE)
        units = [{'id': 'u', 'source_id': 'b1', 'start': 0, 'end': r[0]['start'] + 3, 'speaker': 's', 'text': SHIP[:r[0]['start'] + 3]}]
        out, unmapped = ra.to_corrections(r, units, spoken_of)
        self.assertEqual(out, {}); self.assertIn('no single unit', unmapped[0]['reason'])

    def test_worker_guard_rejects_word_changes(self):
        with self.assertRaises(ValueError):ra.check_spoken_edit('Атом — пять', 'Атом-шесть')
        with self.assertRaises(ValueError):ra.check_spoken_edit('Атом-пять', 'Атом-пять')



class ConsensusTests(unittest.TestCase):
    def test_only_repairs_with_the_same_resulting_paragraph_are_confirmed(self):
        from rimsky.reading_anomalies import validate, consensus
        blocks = [{'id': 'b1', 'text': 'Дым над «Атоме — 5» не рассеялся.'},
                  {'id': 'b2', 'text': '— я проверил заряд Цестуса — 15,8 % — Придется рискнуть.'}]
        luna, _ = validate({'repairs': [
            {'block_id': 'b1', 'original': '«Атоме — 5»', 'replacement': '«Атоме-5»', 'kind': 'dash_as_hyphen', 'rationale': 'x'},
            {'block_id': 'b2', 'original': 'Цестуса — 15,8', 'replacement': 'Цестуса-15,8', 'kind': 'dash_as_hyphen', 'rationale': 'x'}]}, blocks)
        haiku, _ = validate({'repairs': [
            {'block_id': 'b1', 'original': 'Атоме — 5', 'replacement': 'Атоме-5', 'kind': 'dash_as_hyphen', 'rationale': 'y'}]}, blocks)
        confirmed, unconfirmed = consensus(luna, haiku, blocks)
        self.assertEqual([r['block_id'] for r in confirmed], ['b1'])
        self.assertEqual([r['block_id'] for r in unconfirmed], ['b2'])
        self.assertEqual(consensus(luna, [], blocks)[0], [])

if __name__ == '__main__':unittest.main()
