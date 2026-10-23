"""Create a reviewable Book 1 pronunciation candidate from reported words.

Source EPUB text and the prior generation stay unchanged. The accented spoken
text is a TTS hypothesis; listening review is still required.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from runtime.rimsky.store import Store
from runtime.rimsky.util import AppError

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'training-work' / 'book1-listening-app'
BASE = '1b15b833cc8ee135b527c30c'
BOOK = '676d6ad200230b7cfde56a63fd1d8d28'

# Correct lexical stresses supported by the existing RUAccent audit. The
# combining acute is kept out of the EPUB and applied only to the TTS prompt.
STRESSES = {
    'долгожданный': 'долгожда́нный',
    'поняли': 'по́няли',
    'тикает': 'ти́кает',
    'философы': 'фило́софы',
    'внеземным': 'внеземны́м',
    'мохнатая': 'мохна́тая',
    'чертежей': 'чертеже́й',
    'мохнатый': 'мохна́тый',
    'очередную': 'очередну́ю',
    'боролись': 'боро́лись',
    'замаскированный': 'замаскиро́ванный',
    'сигнала': 'сигна́ла',
    'энтузиасты-одиночки': 'энтузиа́сты-одино́чки',
    'свистом': 'сви́стом',
    'Адреналин': 'Адренали́н',
    'сетевым': 'сетевы́м',
    'читерских': 'чи́терских',
    'места': 'ме́ста',
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply:
        raise AppError('Combining acute marks were rejected in listening review; this candidate must not be applied again.')
    store = Store(DATA)
    try:
        generation = store.generation(BASE)
        if generation['book_id'] != BOOK:
            raise AppError('Unexpected Book 1 generation.')
        segments = {segment['id']: segment for segment in generation['segments']}
        grouped = defaultdict(list)
        unresolved = []
        for report in store.stress_reports(BOOK):
            if report['generation_id'] != BASE:
                continue
            word = report['selected_text']
            if word in STRESSES:
                grouped[report['segment_id']].append((word, report['id']))
            else:
                unresolved.append({'report_id': report['id'], 'selection': word,
                                   'reason': 'Phrase, pacing issue, or pronunciation requiring source listening.'})
        changes, audit = [], []
        for segment_id, reports in grouped.items():
            segment = segments[segment_id]
            spoken = segment['spoken']
            for word, report_id in dict(reports).items():
                if spoken.count(word) != 1:
                    raise AppError(f'Reported word {word!r} is not unique in {segment_id}.')
                spoken = spoken.replace(word, STRESSES[word], 1)
                audit.append({'report_id': report_id, 'segment_id': segment_id,
                              'source_word': word, 'spoken_candidate': STRESSES[word]})
            changes.append({'id': segment_id, 'spoken': spoken})
        if len(audit) != sum(len(items) for items in grouped.values()):
            raise AppError('Some repeated reports were not included in the candidate.')
        result = {'base_generation': BASE, 'candidate_generation': None,
                  'changed_segments': len(changes), 'mapped_reports': audit,
                  'unresolved_reports': unresolved, 'human_audio_reviewed': False}
        if args.apply:
            if generation['status'] != 'paused':
                raise AppError('Pause the base generation before applying the stress candidate.')
            existing = next((g for g in store.generation_list(BOOK)
                             if g['parent_id'] == BASE and g['name'] == 'Book 1 · reported stress candidate'), None)
            if existing:
                raise AppError(f'Candidate already exists: {existing["id"]}')
            candidate = store.revise(BASE, changes, 'Book 1 · reported stress candidate')
            result['candidate_generation'] = candidate['id']
            (DATA / 'reported-stress-candidate.json').write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == '__main__':
    main()
