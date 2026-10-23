export type IllustrationRun = {id: string; book_id: string; status: string; created: string; error: string; style: string;
  progress_complete?: number; progress_total?: number; replace_existing?: boolean;
  resume?: {remaining: number; retry_failed: boolean; local_only: boolean} | null;
  excluded?: {source_id: string; chapter_id: string; chapter: string; opening: string; characters: number; reason: string}[];
  items: {source_id: string; status: string; error: string; visual_id: string | null; replaced_visual_ids?: string[]}[]};

export function illustrationRunning(run: Pick<IllustrationRun, 'status'>) {
  return ['queued', 'running'].includes(run.status);
}

export function illustrationResume(run: Pick<IllustrationRun, 'status' | 'resume'>) {
  const plan = run.resume;
  return plan && !illustrationRunning(run) ? {remaining: plan.remaining, retryFailed: plan.retry_failed, localOnly: plan.local_only} : null;
}

export function illustrationElsewhere(activeRun: string | null, runs: Pick<IllustrationRun, 'id'>[]) {
  return Boolean(activeRun && !runs.some(run => run.id === activeRun));
}

const runErrors: Record<string, string> = {
  'Interrupted request. Check before explicitly retrying; provider usage may already have occurred.': 'Запрос был прерван. Проверьте результат перед явным повтором: использование у провайдера могло уже произойти.',
  'The saved illustration was deleted.': 'Сохранённая иллюстрация удалена.',
  'A saved illustration was deleted. Resume the run to regenerate it.': 'Сохранённая иллюстрация удалена. Продолжите задачу, чтобы создать её заново.',
  'The saved illustration is missing or invalid.': 'Сохранённая иллюстрация отсутствует или повреждена.',
  'A saved illustration is missing or invalid. Resume the run to regenerate it.': 'Сохранённая иллюстрация отсутствует или повреждена. Продолжите задачу, чтобы создать её заново.',
  'Could not start illustration preparation. Resume to try again.': 'Не удалось начать подготовку иллюстраций. Продолжите задачу, чтобы попробовать снова.',
  'Online illustration permission was disabled. Remaining paragraphs were not sent.': 'Разрешение на сетевые иллюстрации отключено. Оставшиеся абзацы не отправлены.',
  'Paragraph text changed since the illustration plan was created.': 'Текст абзаца изменился после создания плана иллюстраций.',
  'Unknown illustration passage.': 'Абзац для иллюстрации не найден.',
  'Book not found.': 'Книга не найдена.',
  'Codex CLI is not installed or is not on PATH. No automatic installation or API fallback is used.': 'Codex CLI не установлен или не найден в PATH. Автоматическая установка и переход на API не используются.',
  'Illustration preparation stopped before dispatch.': 'Подготовка иллюстраций остановлена до отправки запроса.',
  'Illustration request interrupted. Provider completion is uncertain; retry requires explicit approval.': 'Запрос иллюстрации прерван. Неизвестно, завершил ли его провайдер; повтор требует явного согласия.',
  'Illustration request exceeded its time or output limit. It will not be retried automatically.': 'Запрос иллюстрации превысил лимит времени или объёма вывода. Автоматического повтора не будет.',
  'Codex did not finish the illustration. Check CLI sign-in and image-tool availability; there is no automatic retry.': 'Codex не завершил иллюстрацию. Проверьте вход в CLI и доступность инструмента изображений; автоматического повтора нет.',
  'Illustration request exceeded its output limit. It will not be retried automatically.': 'Запрос иллюстрации превысил лимит объёма вывода. Автоматического повтора не будет.',
  'Codex did not return a bounded result file.': 'Codex не вернул файл результата допустимого размера.',
  'Codex result is not valid JSON.': 'Результат Codex не является корректным JSON.',
  'Codex result does not match the requested paragraph.': 'Результат Codex не соответствует запрошенному абзацу.',
  'Codex returned an invalid image path or oversized file.': 'Codex вернул недопустимый путь к изображению или слишком большой файл.',
  'Choose a valid PNG, JPEG, or WebP image of at most 20 MiB and 30 million pixels.': 'Выберите корректное изображение PNG, JPEG или WebP размером не более 20 МиБ и 30 млн пикселей.',
  'This paragraph exceeds the 16,000-character illustration limit and was skipped. No text was truncated.': 'Абзац превышает лимит иллюстраций в 16 000 символов и пропущен. Текст не сокращался.',
};
const runErrorPrefixes: Array<[string, string]> = [['Codex image generation unavailable: ', 'Создание изображений в Codex недоступно: ']];

export function runError(message: string, language: 'en' | 'ru'): string {
  if (language !== 'ru') return message;
  const prefix = runErrorPrefixes.find(([english]) => message.startsWith(english));
  return runErrors[message] ?? (prefix ? prefix[1] + message.slice(prefix[0].length) : message);
}

const imageRequests = new Set<string>();
const imageListeners = new Set<() => void>();
const notifyImageRequests = () => imageListeners.forEach(listener => listener());

export function subscribeImageRequests(listener: () => void) {
  imageListeners.add(listener);
  return () => { imageListeners.delete(listener); };
}

export function imageRequestPending(key: string) {
  return imageRequests.has(key);
}

export async function trackImageRequest<T>(key: string, task: () => Promise<T>): Promise<T> {
  if (imageRequests.has(key)) throw new Error('An API image request for this paragraph is still running.');
  imageRequests.add(key);
  notifyImageRequests();
  try {
    return await task();
  } finally {
    imageRequests.delete(key);
    notifyImageRequests();
  }
}
