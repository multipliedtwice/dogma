import {useEffect, useState} from 'react';
import {Check, Link2, LoaderCircle, Plus, Sparkles, Star} from 'lucide-react';
import {api, isRuntimeLoss} from '../lib/api';
import {copyWhenReady} from '../lib/clipboard';
import {count, locale, operationMessage, translator} from '../lib/i18n';
import type {Recommendation, RecommendationGenre, Recommendations, Settings} from '../lib/types';
import {cn} from '../lib/utils';
import {AssetImage} from './Media';
import {Button} from './ui/button';

const PREVIEW = 8;
// Survives the section unmounting while a book is open, so returning to the library shows the last list at once.
let lastResult: {key: string; value: Recommendations} | null = null;

export function HomeRecommendations({settings, libraryKey, update}: {settings: Settings; libraryKey: string; update: (change: Partial<Settings>) => Promise<void>}) {
  const language = settings.language;
  const t = translator(language);
  const online = settings.online_recommendations;
  const chosen = settings.recommendation_genres;
  const [genres, setGenres] = useState<RecommendationGenre[]>([]);
  const request = chosen.join(',');
  const key = `${request}|${libraryKey}`;
  const [result, setResult] = useState<Recommendations | null>(() => lastResult?.key === key ? lastResult.value : null);
  const [loading, setLoading] = useState(false);
  const [enabling, setEnabling] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);

  useEffect(() => {
    let live = true;
    api<RecommendationGenre[]>('recommendation_genres').then(list => { if (live) setGenres(list); }, () => undefined);
    return () => { live = false; };
  }, []);

  useEffect(() => {
    // Wait for the opt-in to be saved; the runtime refuses lookups until then.
    if (!online || enabling) return;
    let live = true;
    // Debounced so tapping several chips in a row sends one request.
    const timer = setTimeout(() => {
      setLoading(true); setError('');
      api<Recommendations>('recommendations', {genres: chosen})
        .then(next => { lastResult = {key, value: next}; if (live) setResult(next); },
          reason => { if (live && !isRuntimeLoss(reason)) setError(operationMessage(reason, language)); })
        .finally(() => { if (live) setLoading(false); });
    }, 450);
    return () => { live = false; clearTimeout(timer); setLoading(false); };
  }, [online, enabling, request, libraryKey, retry]);

  useEffect(() => { if (copied === null) return; const timer = setTimeout(() => setCopied(null), 2500); return () => clearTimeout(timer); }, [copied]);

  const enable = () => { setEnabling(true); void update({online_recommendations: true}).finally(() => setEnabling(false)); };
  const toggle = (id: string) => void update({recommendation_genres: chosen.includes(id) ? chosen.filter(item => item !== id) : [...chosen, id]});
  const label = (genre: RecommendationGenre) => language === 'ru' ? genre.ru : genre.en;
  const genreLabel = (id: string) => { const genre = genres.find(item => item.id === id); return genre ? label(genre) : id; };
  const quote = (text: string) => language === 'ru' ? `«${text}»` : `“${text}”`;
  const reason = (item: Recommendation) => item.reasons.slice(0, 2).map(entry => entry.kind === 'book'
    ? `${t('recommendationsSimilar')} ${quote(entry.title)}` : `${t('recommendationsInGenre')} ${quote(genreLabel(entry.id))}`).join(' · ');
  const copy = (item: Recommendation) => void copyWhenReady(Promise.resolve(item.url)).then(() => setCopied(item.work_id), () => undefined);

  const items = result?.items || [];
  const visible = expanded ? items : items.slice(0, PREVIEW);
  const unmatched = Boolean(result && result.seeds.length && !result.seeds.some(seed => seed.work_id) && !chosen.length);
  const retryButton = <Button variant="outline" size="sm" disabled={loading} onClick={() => setRetry(value => value + 1)}>{t('recommendationsRetry')}</Button>;

  return <section className="recommendations" aria-labelledby="recommendations-title" aria-busy={loading || enabling}>
    <div className="recommendations-head">
      <div><span className="eyebrow">{t('recommendationsEyebrow')}</span><h2 id="recommendations-title">{t('recommendationsTitle')}</h2><p>{t('recommendationsText')}</p></div>
      {online && loading && result && <span role="status" aria-label={t('recommendationsLoading')}><LoaderCircle size={18} className="spin" aria-hidden="true"/></span>}
    </div>
    {genres.length > 0 && <div className="genre-chips" role="group" aria-label={t('recommendationsGenres')}>
      {genres.map(genre => { const on = chosen.includes(genre.id); return <button type="button" key={genre.id} className={cn('genre-chip', on && 'on')} aria-pressed={on} onClick={() => toggle(genre.id)}>{on ? <Check size={14}/> : <Plus size={14}/>}{label(genre)}</button>; })}
    </div>}
    {!online ? <div className="recommendations-consent">
      <Sparkles size={20}/><div><strong>{t('recommendationsConsentTitle')}</strong><p>{t('recommendationsConsent')}</p></div>
      <Button disabled={enabling} onClick={enable}>{t('recommendationsEnable')}</Button>
    </div> : error ? <div className="recommendations-note" role="alert"><span>{t('recommendationsFailed')} {error}</span>{retryButton}</div>
      : !result ? <div className="recommendation-grid" role="status" aria-label={t('recommendationsLoading')}>{Array.from({length: 4}, (_, index) => <div key={index} className="recommendation-card skeleton"><span className="recommendation-cover"/><span/></div>)}</div>
      : !items.length ? result.failures ? <div className="recommendations-note" role="alert"><span>{t('recommendationsFailed')}</span>{retryButton}</div>
        : <p className="small-empty">{unmatched ? t('recommendationsUnmatched') : t('recommendationsEmpty')}</p>
      : <>
        {result.failures > 0 && <div className="recommendations-note"><span>{t('recommendationsPartial')}</span>{retryButton}</div>}
        <div className="recommendation-grid">{visible.map(item => <article className="recommendation-card" key={item.work_id}>
          <div className="recommendation-cover" aria-hidden="true">{item.cover ? <AssetImage kind="fantlab_cover" id={item.cover} alt="" language={language}/> : <span>{item.title}</span>}</div>
          <div className="recommendation-body">
            <h3 title={item.original_title && item.original_title !== item.title ? item.original_title : undefined}>{item.title}</h3>
            <p className="recommendation-author">{[item.authors.join(', '), item.year, item.kind_label].filter(Boolean).join(' · ')}</p>
            {item.rating !== null && <p className="recommendation-stats" title={t('recommendationsRating')}><Star size={13} aria-hidden="true"/><strong>{item.rating.toLocaleString(locale(language), {minimumFractionDigits: 2, maximumFractionDigits: 2})}</strong><span>{count(language, item.voters, 'ratings')}</span></p>}
            <p className="recommendation-reason">{reason(item)}</p>
            {item.description && <p className="recommendation-description">{item.description}</p>}
            <button type="button" className="recommendation-link" onClick={() => copy(item)}>{copied === item.work_id ? <Check size={13}/> : <Link2 size={13}/>}{copied === item.work_id ? t('recommendationsCopied') : t('recommendationsCopyLink')}</button>
          </div>
        </article>)}</div>
        {items.length > PREVIEW && <Button variant="outline" size="sm" className="recommendations-more" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? t('recommendationsLess') : t('recommendationsMore')}</Button>}
      </>}
  </section>;
}
