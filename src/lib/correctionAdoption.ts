import type {Book, Generation, StressReport} from './types';

export type CorrectionPublication = {bookId:string;generationId:string;affectedSegmentIds:string[];affectedSourceIds:string[]};
type Selection = {bookId:string;generationId:string;epoch:number};
type Services = {
  selection:()=>Selection;
  load:(publication:CorrectionPublication)=>Promise<{book:Book;generation:Generation}>;
  adopt:(book:Book,generation:Generation,publication:CorrectionPublication)=>boolean;
  offer:(publication:CorrectionPublication)=>void;
};

/** Track saved requests across navigation; a completed historical report alone
 * never authorizes interrupting the listener's current selection. */
export function createCorrectionAdoption() {
  const watching=new Map<string,Selection>();
  const handled=new Set<string>();
  let busy=false;
  const watch=(row:StressReport,selected:Selection,submitted=false)=>{
    const queue=row.retake_queue;
    if(queue&&(['queued','running','paused'].includes(queue.status)||submitted&&queue.status==='applied'&&queue.published_generation_id)&&!watching.has(row.id)
      &&row.book_id===selected.bookId&&[row.generation_id,...queue.origin_generation_ids||[]].includes(selected.generationId))
      watching.set(row.id,{...selected});
  };
  return {
    watch,
    bookIds:()=>[...new Set([...watching.values()].map(row=>row.bookId))],
    async receive(rows:StressReport[],services:Services) {
      const selected=services.selection();
      for(const row of rows) {
        watch(row,selected);
      }
      if(busy)return;
      const ready=rows.filter(row=>row.retake_queue?.status==='applied'&&row.retake_queue.published_generation_id);
      const candidate=ready.find(row=>watching.has(row.id)&&!handled.has(row.retake_queue!.published_generation_id!));
      if(!candidate){
        const completed=ready.find(row=>!handled.has(row.retake_queue!.published_generation_id!)
          &&row.book_id===selected.bookId&&[row.generation_id,...row.retake_queue!.origin_generation_ids||[]].includes(selected.generationId));
        if(completed){const queue=completed.retake_queue!;handled.add(queue.published_generation_id!);
          services.offer({bookId:completed.book_id,generationId:queue.published_generation_id!,
            affectedSegmentIds:queue.affected_segment_ids||[],affectedSourceIds:queue.affected_source_ids||[]});}
        return;
      }
      const generationId=candidate.retake_queue!.published_generation_id!;
      const group=ready.filter(row=>row.retake_queue!.published_generation_id===generationId);
      const origin=watching.get(candidate.id)!;
      const publication={bookId:candidate.book_id,generationId,
        affectedSegmentIds:[...new Set(group.flatMap(row=>row.retake_queue!.affected_segment_ids||[]))],
        affectedSourceIds:[...new Set(group.flatMap(row=>row.retake_queue!.affected_source_ids||[]))]};
      const unchanged=()=>{const now=services.selection();return now.bookId===origin.bookId
        &&now.generationId===origin.generationId&&now.epoch===origin.epoch;};
      busy=true;
      try {
        if(!unchanged()){services.offer(publication);handled.add(generationId);return;}
        const next=await services.load(publication);
        if(!unchanged()||next.book.id!==publication.bookId||next.generation.id!==generationId
          ||next.generation.book_id!==publication.bookId||!services.adopt(next.book,next.generation,publication))
          services.offer(publication);
        handled.add(generationId);
      } finally {
        busy=false;
        if(handled.has(generationId))for(const row of group)watching.delete(row.id);
      }
    },
  };
}
