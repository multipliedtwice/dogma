"""Wait for and merge the three complete-book GPT context-analysis lanes."""
import json
import time
from pathlib import Path

from prepare_book_label_pilot import ROOT, save

FOLDER=ROOT/'training-work/character-rb2/full'
RESULTS=[FOLDER/f'context-lane-{name}.json' for name in ('a','b','c')]
CHECKPOINTS=[FOLDER/name for name in ('context','context-lane-b','context-lane-c')]


def main():
    while not all(path.exists() for path in RESULTS):
        time.sleep(30)
    expected={json.loads(line)['id'] for line in
              (FOLDER/'source/speech-inputs.jsonl').read_text(encoding='utf-8').splitlines()}
    analyzed=set()
    for directory in CHECKPOINTS:
        for path in directory.glob('*.json'):
            saved=json.loads(path.read_text(encoding='utf-8'))
            analyzed.update(saved.get('target_ids') or
                            [passage['id'] for passage in saved['raw']['passages']])
    if analyzed!=expected:
        missing=sorted(expected-analyzed)[:10]
        extra=sorted(analyzed-expected)[:10]
        raise ValueError(f'Context lanes do not cover the full source: missing={missing}, extra={extra}')
    characters={};passages=[];passage_ids=set()
    for path in RESULTS:
        document=json.loads(path.read_text(encoding='utf-8'))
        for character in document['characters']:
            current=characters.get(character['id'])
            if current is None:
                characters[character['id']]=character
            else:
                if current['name']!=character['name']:
                    raise ValueError('Character ID collision across context lanes')
                for field in ('aliases','evidence'):
                    current[field]=list(dict.fromkeys(current[field]+character[field]))
        for passage in document['passages']:
            if passage['id'] in passage_ids:
                raise ValueError('Duplicate analyzed passage across context lanes: '+passage['id'])
            passage_ids.add(passage['id']);passages.append(passage)
    passages.sort(key=lambda row:(row['ordinal'],row['id']))
    save(FOLDER/'context.json',{'schema':1,
        'basis':'Three contiguous production Codex analysis lanes, validated separately then merged; text intentions are not recorded audio labels',
        'characters':list(characters.values()),'passages':passages,
        'coverage':{'source_segments':len(expected),'analyzed_target_ids':len(analyzed),'lanes':3}})
    print(f'Merged {len(passages)} analyzed passages covering {len(expected)} source segments.',flush=True)


if __name__=='__main__':main()
