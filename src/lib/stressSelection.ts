export type StressSelection = {sourceId:string; text:string; textOffset:number|null};

const russianVowel=/[\u0430\u0435\u0451\u0438\u043e\u0443\u044b\u044d\u044e\u044f\u0410\u0415\u0401\u0418\u041e\u0423\u042b\u042d\u042e\u042f]/u;
const russianWord=/^[\u0410-\u042f\u0430-\u044f\u0401\u0451]+(?:[-\u2011][\u0410-\u042f\u0430-\u044f\u0401\u0451]+)*$/u;

/** Every explicit stress placement the selected Russian word can express. */
export function stressVariants(value:string):string[] {
  const word=value.normalize('NFC').replaceAll('\u0301','');
  if(!russianWord.test(word))return [];
  const letters=Array.from(word),variants:string[]=[];
  for(let index=0;index<letters.length;index++)if(russianVowel.test(letters[index])){
    const marked=letters.slice();marked[index]+='\u0301';variants.push(marked.join(''));
  }
  return variants;
}

/**
 * A typed pronunciation of the selected word: the same letters, except that chosen е/Е may become
 * э/Э or ё/Ё, with one stress mark (ё is its own mark). Mirrors rimsky.respelling on the runtime;
 * returns the NFC form to report or null.
 */
export function respelledStress(selected:string,typed:string):string|null {
  const word=Array.from(selected.normalize('NFC').replaceAll('́','')),value=typed.normalize('NFC').trim();
  if(!russianWord.test(word.join('')))return null;
  const letters=Array.from(value),plain:string[]=[],marks:number[]=[];
  for(const letter of letters){if(letter==='́')marks.push(plain.length-1);else plain.push(letter);}
  if(plain.length!==word.length||marks.some(index=>index<0||!russianVowel.test(plain[index])))return null;
  const yo:number[]=[];
  for(let index=0;index<word.length;index++){
    if(plain[index]===word[index])continue;
    const allowed=word[index]==='е'?'эё':word[index]==='Е'?'ЭЁ':'';
    if(!allowed.includes(plain[index]))return null;
    if(plain[index]==='ё'||plain[index]==='Ё')yo.push(index);
  }
  const respelled=plain.some((letter,index)=>letter!==word[index]);
  if(!respelled||marks.length+yo.length!==1||marks.some(index=>yo.includes(index)))return null;
  return value;
}


/** A normal stress choice and a permitted respelling share the same Save action. */
export function stressCorrection(selected:string,typed:string):string|null {
  const value=typed.normalize('NFC').trim();
  return stressVariants(selected).some(variant=>variant.normalize('NFC')===value)
    ?value:respelledStress(selected,value);
}

type Comparable={text:string;offsets:number[]};

function comparable(value:string):Comparable {
  const characters=Array.from(value),output:string[]=[],offsets:number[]=[];
  let whitespace=false;
  for(let index=0;index<characters.length;index++){
    // EPUB layout hints are not part of the spoken/source text. Ignore them
    // during matching while retaining source offsets for every real character.
    if(/[\u00ad\u200b\u200c\u200d\u2060\ufeff]/u.test(characters[index]))continue;
    if(/\s/u.test(characters[index])){
      if(!whitespace){output.push(' ');offsets.push(index);}
      whitespace=true;
    }else{
      output.push(characters[index]);offsets.push(...Array(characters[index].length).fill(index));whitespace=false;
    }
  }
  return {text:output.join(''),offsets};
}

function distance(left:string,right:string):number {
  const a=Array.from(left),b=Array.from(right);
  let previous=Array.from({length:b.length+1},(_,index)=>index);
  for(let row=1;row<=a.length;row++){
    const current=[row];
    for(let column=1;column<=b.length;column++)current[column]=Math.min(
      current[column-1]+1,previous[column]+1,previous[column-1]+Number(a[row-1]!==b[column-1]));
    previous=current;
  }
  return previous[b.length];
}

function candidates(haystack:string,needle:string):number[] {
  const found:number[]=[];
  for(let start=haystack.indexOf(needle);start>=0;start=haystack.indexOf(needle,start+1))found.push(start);
  return found;
}

export function stressSelection(section:HTMLElement, sourceText:string, selection:Selection|null):StressSelection|null {
  if(!selection || selection.isCollapsed || !selection.rangeCount)return null;
  const range=selection.getRangeAt(0);
  if(!section.contains(range.startContainer) || !section.contains(range.endContainer))return null;
  const markup=section.querySelector<HTMLElement>(':scope > div');
  if(!markup || !markup.contains(range.startContainer) || !markup.contains(range.endContainer))return null;
  const raw=selection.toString();
  if(!raw.trim() || Array.from(raw).length>200)return null;
  const before=range.cloneRange();before.selectNodeContents(markup);before.setEnd(range.startContainer,range.startOffset);
  const renderedBefore=comparable(before.toString()).text;
  const renderedAfterText=typeof markup.textContent==='string'?
    comparable(markup.textContent).text.slice(renderedBefore.length+comparable(raw).text.length):'';
  const source=comparable(sourceText),primary=comparable(raw).text.trim();
  const alternatives=[primary];
  // EPUB footnote references can be visible in the DOM but absent from the
  // imported prose. The narrow fallback removes only a short attached marker.
  const withoutReference=primary.replace(/(?<=\p{L})[0-9¹²³⁰⁴⁵⁶⁷⁸⁹*†‡]{1,3}(?=\s|[.,;:!?])/gu,'');
  if(withoutReference!==primary)alternatives.push(withoutReference);
  let best:{start:number;needle:string;score:number}|null=null;
  for(const needle of alternatives){
    if(!needle)continue;
    for(const start of candidates(source.text,needle)){
      const beforeSource=source.text.slice(Math.max(0,start-48),start);
      const beforeRendered=renderedBefore.slice(-48);
      const afterSource=source.text.slice(start+needle.length,start+needle.length+48);
      const afterRendered=renderedAfterText.slice(0,48);
      const score=distance(beforeSource,beforeRendered)+(afterRendered?distance(afterSource,afterRendered):0);
      if(!best||score<best.score)best={start,needle,score};
    }
  }
  if(!best)return null;
  const first=source.offsets[best.start],last=source.offsets[best.start+best.needle.length-1]+1;
  const exact=Array.from(sourceText).slice(first,last).join('');
  return {sourceId:section.dataset.sourceId||'',text:exact,textOffset:first};
}
