import {useEffect, useState} from 'react';
import {AlertCircle, CircleCheck, Clock, LoaderCircle, PauseCircle} from 'lucide-react';
import {api, isRuntimeLoss} from '../lib/api';
import {formatRemaining} from '../lib/eta';
import {locale} from '../lib/i18n';
import type {ExternalJob} from '../lib/types';

const POLL_MS = 15000;

// Supervised narration jobs that run outside the app (the Higgs preview). Read-only.
export function ExternalJobs({language}: {language: 'en' | 'ru'}) {
  const ru = language === 'ru';
  const [jobs, setJobs] = useState<ExternalJob[]>([]);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const result = await api<{jobs: ExternalJob[]}>('external_jobs');
        if (!stopped) setJobs(result.jobs);
      } catch (reason) {
        if (isRuntimeLoss(reason)) return;
      }
      if (!stopped) timer = setTimeout(() => void poll(), POLL_MS);
    };
    void poll();
    return () => { stopped = true; if (timer) clearTimeout(timer); };
  }, []);
  if (!jobs.length) return null;
  return <section className="external-jobs" aria-label={ru ? 'Фоновая озвучка' : 'Background narration'}>
    {jobs.map(job => {
      const done = job.chapters?.every(c => c.complete && !c.open_flags && !c.held.length);
      const icon = done ? <CircleCheck size={16}/> : job.worker_alive ? <LoaderCircle size={16} className="spin"/> : job.status === 'unreadable' ? <AlertCircle size={16}/> : <PauseCircle size={16}/>;
      const state = done ? (ru ? 'готово' : 'complete') : job.worker_alive ? (ru ? 'идёт генерация' : 'generating') : (ru ? 'остановлено' : 'stopped');
      const eta = job.worker_alive && job.eta_seconds != null ? formatRemaining(language, job.eta_seconds) : '';
      return <div key={job.id} className="external-job">
        <div className="external-job-head">{icon}<strong>{job.title}</strong><span>· {state}</span>
          <span className="external-job-percent">{(job.percent ?? 0).toLocaleString(locale(language))}%</span></div>
        <progress value={job.percent ?? 0} max={100} aria-label={ru ? 'Проверенные фрагменты' : 'Verified passages'}/>
        <div className="external-job-meta">
          <span>{ru ? `Проверено ${job.verified} из ${job.total}` : `${job.verified} of ${job.total} passages verified`}</span>
          {job.held > 0 && <span>· {ru ? `${job.held} ждут вашего решения` : `${job.held} waiting for your review`}</span>}
          {eta && <span><Clock size={12}/> {eta} {ru ? '(оценка)' : '(estimate)'}</span>}
        </div>
        <ul className="external-job-chapters">{job.chapters?.map(c => <li key={c.id}>
          <span>{c.title}</span>
          <span>{c.complete ? (c.open_flags ? (ru ? `доступна, ${c.open_flags} проверок открыто` : `playable, ${c.open_flags} checks open`) : (ru ? 'готова' : 'ready'))
            : [c.published_minutes ? (ru ? `доступно ${c.published_minutes} мин` : `${c.published_minutes} min playable`) : '',
               c.held.length ? (ru ? `ждёт решения: ${c.held.length}` : `waiting on ${c.held.length} review${c.held.length > 1 ? 's' : ''}`) : '']
              .filter(Boolean).join(' · ') || (ru ? 'готовится' : 'preparing')}</span>
          <span>{c.verified}/{c.total}</span></li>)}</ul>
        {!job.worker_alive && job.error && <p className="external-job-error">{job.error}</p>}
      </div>;
    })}
  </section>;
}
