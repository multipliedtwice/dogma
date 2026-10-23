"""Original offsets for the auditor's sustained-sound token units."""
import re
from rimsky.speech_quality import word_tokens,spoken_word_tokens


def positions(text):
    chars=[];offsets=[]
    for i,char in enumerate(text):
        if char!='\u0301':chars.append(char);offsets.append(i)
    collapses={}
    for match in re.finditer(r'(?<!\w)([А-Яа-яЁё]+)((?:[-‐‑–][А-Яа-яЁё])+)(?!\w)',text):
        word,tail=match.groups()
        if all(letter.casefold()==word[-1].casefold() for letter in re.findall('[А-Яа-яЁё]',tail)):
            collapses[match.start()]=(word,match.end())
    cleaned=''.join(chars);words=list(re.finditer(r'[^\W_]+',cleaned,re.UNICODE));result=[];i=0
    while i<len(words):
        first=words[i];start=offsets[first.start()];word,end=collapses.get(start,(first[0],offsets[first.end()-1]+1))
        j=i+1
        while j<len(words) and offsets[words[j].start()]<end:j+=1
        while end<len(text) and text[end]=='\u0301':end+=1
        result.append((word_tokens(word)[0],start,end));i=j
    if [row[0] for row in result]!=spoken_word_tokens(text):
        raise ValueError('Spoken offset units differ from the immutable word comparator')
    return result
