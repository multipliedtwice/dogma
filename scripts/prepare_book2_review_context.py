"""Read-only neighboring text for the Book 2 pilot cards."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def build(board,book_path,protected):
    book=json.loads(book_path.read_text(encoding='utf-8'))
    candidates=board/'candidates.jsonl'
    digest=hashlib.sha256(candidates.read_bytes()).hexdigest()
    scope={json.loads(line)['source_id'] for line in candidates.read_text(encoding='utf-8').splitlines()}
    contexts={}
    for chapter in book['chapters']:
        blocks=[block for block in chapter['blocks'] if block.get('text','').strip()]
        for i,block in enumerate(blocks):
            if block['id'] not in scope:continue
            contexts[block['id']]={'chapter':chapter['title'],
                'before':blocks[i-1]['text'] if i else '',
                'current':block['text'],
                'after':blocks[i+1]['text'] if i+1<len(blocks) else ''}
    if contexts.keys()!=scope:raise ValueError('Missing source context')
    payload={'schema':1,'candidates_sha256':digest,'sources':contexts}
    data=(json.dumps(payload,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    (board/'source-context.json').write_bytes(data)
    target=protected/'metadata/source-context.json';target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    manifest=protected/'archive-manifest.json'
    if manifest.exists():
        m=json.loads(manifest.read_text(encoding='utf-8'))
        m['metadata']['source-context.json']=hashlib.sha256(data).hexdigest()
        manifest.write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Prepared context around {len(contexts)} pilot source paragraphs.')


def main():
    protected=ROOT/'manual-labeling/reality-benders-2'
    build(ROOT/'training-work/character-rb2/fine-tune-all',
          protected/'metadata/source-book.json',protected)


if __name__=='__main__':main()
