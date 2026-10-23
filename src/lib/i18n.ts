const messages={
  bookTools:['Book tools','Действия с книгой'],bookPreparation:['Book preparation','Подготовка книги'],nowReading:['NOW READING','ВЫ ЧИТАЕТЕ'],localLibrary:['LOCAL LIBRARY','ЛОКАЛЬНАЯ БИБЛИОТЕКА'],findChapter:['Find a chapter…','Найти главу…'],savedLocally:['Saved locally','Сохранено локально'],prepareChapter:['Prepare this chapter','Озвучить главу'],importNotes:['Import notes','Замечания к импорту'],

  audiobookPlayer:['Audiobook player','Аудиоплеер'],playbackControls:['Playback controls','Управление воспроизведением'],
  playbackSettings:['Playback settings','Настройки плеера'],playbackSpeed:['Playback speed','Скорость'],volume:['Volume','Громкость'],music:['Background music','Фоновая музыка'],musicVolume:['Music volume','Громкость музыки'],
  chapterTimeline:['Chapter position','Позиция в главе'],preparedTimeline:['Prepared audio only','Только готовое аудио'],passageTimeline:['This passage · earlier audio is not ready','Отрывок · предыдущее аудио не готово'],
  previousChapter:['Previous chapter','Предыдущая глава'],nextChapter:['Next chapter','Следующая глава'],back15:['Back 15 seconds','Назад на 15 секунд'],forward15:['Forward 15 seconds','Вперёд на 15 секунд'],sleepActive:['Sleep timer is active','Таймер сна включён'],

  library:['Library','Библиотека'],openBook:['Open book','Открыть книгу'],settings:['Settings','Настройки'],local:['On your computer','На вашем компьютере'],
  search:['Search your library','Поиск в библиотеке'],books:['books','книг'],chapters:['Chapters','Главы'],bookmarks:['Bookmarks','Закладки'],generations:['Generations','Озвучки'],
  read:['Read','Читать'],illustrated:['Illustrated','Иллюстрации'],study:['Study','Изучать'],export:['Export','Экспорт'],prepare:['Prepare audiobook','Озвучить книгу'],
  continue:['Continue','Продолжить'],play:['Play','Слушать'],pause:['Pause','Пауза'],resume:['Resume preparation','Продолжить озвучку'],ready:['Ready','Готово'],
  paused:['Paused','Приостановлено'],failed:['Needs attention','Нужна проверка'],pending:['Not generated','Не озвучено'],queued:['Queued','В очереди'],analyzing:['Reading characters','Анализ персонажей'],casting:['Assigning voices','Назначение голосов'],rendering:['Generating audio','Создание аудио'],aligning:['Aligning words','Синхронизация слов'],review:['Cast review','Проверка ролей'],held:['Held for review','Ожидает проверки'],
  delete:['Delete','Удалить'],cancel:['Cancel','Отмена'],save:['Save','Сохранить'],rename:['Rename','Переименовать'],newGeneration:['New generation','Новая озвучка'],
  narration:['Characters & narration','Персонажи и озвучка'],appearance:['Appearance','Оформление'],light:['Light','Светлая'],dark:['Dark','Тёмная'],sepia:['Sepia','Сепия'],
  emptyTitle:['Your next audiobook starts with a book.','Ваша следующая аудиокнига начинается с книги.'],
  emptyText:['Open an EPUB or FB2. Keep the text, give characters their own voices, and listen at your pace.','Откройте EPUB или FB2. Читайте оригинал, слушайте голоса персонажей и сохраняйте аудио.'],
  privacy:['Books and generated audio stay on this computer.','Книги и созданное аудио остаются на этом компьютере.'],
  noAudio:['No audio prepared yet','Аудио ещё не создано'],noGeneration:['Create a generation to automatically cast and narrate this book.','Создайте озвучку: приложение назначит голоса и подготовит аудио.'],
  allChapters:['Entire book','Вся книга'],thisChapter:['Current chapter','Текущая глава'],fontSize:['Text size','Размер текста'],lineHeight:['Line spacing','Межстрочный интервал'],
  models:['Local model pack','Локальные модели'],chooseFolder:['Choose folder','Выбрать папку'],verify:['Check pack','Проверить комплект'],follow:['Follow narration','Следовать за чтением'],
  storage:['Library storage','Хранилище библиотеки'],sleep:['Sleep timer','Таймер сна'],off:['Off','Выключен'],correct:['Correct passage','Исправить отрывок'],
  imported:['Imported','Импортировано'],generated:['Generated','Создано'],voice:['Voice','Голос'],unknown:['Unresolved speaker','Неизвестный говорящий'],narrator:['Narrator','Рассказчик'],
  noBookmarks:['Bookmark a passage to find it here.','Добавьте закладку к отрывку.'],noResults:['No matching books','Книги не найдены'],
  sourceFigure:['Original book figure','Иллюстрация из книги'],addImage:['Attach illustration','Добавить иллюстрацию'],makeImage:['Generate illustration','Создать иллюстрацию'],
  readOnly:['Reading is available. Narration needs a local model pack.','Чтение доступно. Для озвучки нужен локальный комплект моделей.'],
  openingBook:['Opening book','Открытие книги'],fileAssociationFailed:['File association listener failed','Не удалось подключить открытие файлов'],generationRefreshFailed:['Generation refresh failed','Не удалось обновить озвучку'],runtimeRefreshFailed:['Runtime status refresh failed','Не удалось обновить состояние среды'],illustrationRefreshFailed:['Illustration refresh failed','Не удалось обновить иллюстрации'],readingSaveFailed:['Could not save reading position','Не удалось сохранить место чтения'],passageMissing:['The selected passage is not present in this generation.','Выбранного отрывка нет в этой озвучке.'],booksQueued:['Books are queued and will open when the current operation finishes.','Книги поставлены в очередь и откроются после завершения текущей операции.'],busyWait:['Another operation is in progress','Выполняется другая операция'],importingBooks:['Importing books','Импорт книг'],pauseRequested:['Pause requested. Completed audio is kept.','Пауза запрошена. Готовое аудио сохранено.'],repairingAudio:['Checking and repairing saved audio','Проверка и восстановление аудио'],audioVerified:['Saved audio verified. Nothing needed regeneration.','Сохранённое аудио проверено. Повторная озвучка не нужна.'],repairStarted:['Repair started with the frozen cast and model. Healthy audio will be reused.','Восстановление запущено с прежними голосами и моделью. Исправное аудио будет использовано повторно.'],exportingAudio:['Exporting saved audio','Экспорт сохранённого аудио'],selectCompletedChapter:['Select at least one completed chapter.','Выберите хотя бы одну завершённую главу.'],exportCancelledCleanup:['Export cancelled and removed from history, but its temporary local file could not be removed.','Экспорт отменён и удалён из истории, но временный локальный файл удалить не удалось.'],exportCancelled:['Export cancelled. No library copy was retained.','Экспорт отменён. Копия в библиотеке не сохранена.'],exportSaved:['Export saved from existing audio.','Экспорт сохранён из готового аудио.'],preparingAudiobook:['Preparing audiobook','Подготовка аудиокниги'],selectNarratable:['Select at least one section with narratable text.','Выберите хотя бы один раздел с текстом для озвучки.'],createBeforeCorrect:['Create a generation before correcting narration.','Создайте озвучку, прежде чем исправлять её.'],updatingBookmark:['Updating bookmark','Обновление закладки'],bookmarkRemoved:['Bookmark removed.','Закладка удалена.'],bookmarkSaved:['Bookmark saved.','Закладка сохранена.'],closePanelDrop:['Close the panel before dropping books, or use its image attachment control.','Закройте панель перед перетаскиванием книг или используйте в ней кнопку добавления изображения.'],closePanelFirst:['Close the panel before dropping books','Закройте панель перед перетаскиванием книг'],dropBooks:['Drop EPUB or FB2 books to import','Отпустите файлы EPUB или FB2 для импорта'],openingLibrary:['Opening your local library…','Открытие локальной библиотеки…'],sidebarTitle:['Your books. Your pace.','Ваши книги. Ваш темп.'],sidebarText:['Read the original. Listen to its characters. Keep every generation.','Читайте оригинал. Слушайте персонажей. Сохраняйте каждую озвучку.'],hideSidebar:['Hide sidebar','Скрыть боковую панель'],showSidebar:['Show sidebar','Показать боковую панель'],illustratingOnline:['Preparing illustrations online','Создание иллюстраций онлайн'],illustrationStopRequested:['Illustration stop requested. Saved images are kept.','Остановка иллюстраций запрошена. Сохранённые изображения остаются.'],stopIllustrations:['Stop illustrations','Остановить иллюстрации'],importHint:['EPUB & FB2 · Drag a book anywhere to import','EPUB и FB2 · Перетащите книгу в любое место окна'],readyToRead:['Ready to read','Готово к чтению'],savedGenerations:['Saved generations','Сохранённых озвучек'],deletingBook:['Deleting book','Удаление книги'],setupNarration:['Set up local narration','Настроить локальную озвучку'],repairAudio:['Check and repair audio','Проверить и восстановить аудио'],resumingPreparation:['Resuming preparation','Возобновление озвучки'],selectingPack:['Selecting model pack','Выбор комплекта моделей'],checkingPack:['Checking model pack','Проверка комплекта моделей'],openingGeneration:['Opening generation','Открытие озвучки'],renamingGeneration:['Renaming generation','Переименование озвучки'],deletingGeneration:['Deleting generation','Удаление озвучки'],savingCorrection:['Saving corrected version','Сохранение исправленной версии'],imageUnavailable:['Image unavailable · Retry','Изображение недоступно · Повторить'],correctionNeedsAnalysis:['Resume preparation to finish character analysis before making corrections.','Продолжите озвучку, чтобы завершить анализ персонажей, прежде чем вносить исправления.'],
  runtimeDisconnected:['Runtime disconnected','Среда отключена'],runtimeLost:['Lost connection to the local runtime. Your books and saved audio stay on this computer.','Нет связи с локальной средой. Книги и сохранённое аудио остаются на этом компьютере.'],runtimeRestartDesktop:['Still no connection to the local runtime. Quit and reopen the app to restart it.','Связи с локальной средой по-прежнему нет. Закройте и снова откройте приложение, чтобы перезапустить её.'],runtimeRestartBrowser:['Still no connection to the local runtime. Restart it and open the new URL printed in its terminal.','Связи с локальной средой по-прежнему нет. Перезапустите её и откройте новый адрес из терминала.'],retry:['Retry','Повторить'],correctionUnchanged:['Change the spoken text, speaker or delivery to create a corrected generation.','Измените текст, говорящего или подачу, чтобы создать исправленную озвучку.'],
  findChapterLabel:['Find chapter','Найти главу'],editBookmarkNote:['Edit bookmark note','Изменить заметку к закладке'],removeBookmark:['Remove bookmark','Удалить закладку'],removingBookmark:['Removing bookmark','Удаление закладки'],bookmarkNote:['Bookmark note','Заметка к закладке'],dismissError:['Dismiss error','Закрыть ошибку'],clearSearch:['Clear search','Очистить поиск'],removeFromLibrary:['Remove from library','Удалить из библиотеки'],
  generationMissingChapter:['The selected generation does not include this chapter. Open another generation or prepare this chapter.','Выбранная озвучка не включает эту главу. Откройте другую озвучку или озвучьте главу.'],spokenRequired:['Spoken text cannot be empty.','Произносимый текст не может быть пустым.'],generationNameRequired:['Enter a name for this generation.','Введите название озвучки.'],preparingAudio:['Preparing audio','Готовится аудио'],savedOnComputer:['saved on this computer','на этом компьютере'],savingExport:['Saving export','Сохранение экспорта'],deletingExport:['Deleting export','Удаление экспорта'],panelContinues:['You may close this panel; the operation continues.','Панель можно закрыть: операция продолжится.'],noParagraph:['This section has no paragraph to illustrate.','В этом разделе нет абзаца для иллюстрации.'],includePassageChapter:['Include the selected passage’s chapter to listen from here, or cancel and prepare another selection.','Включите главу выбранного отрывка, чтобы слушать с этого места, или отмените и выберите другие главы.'],importRejected:['choose a nonempty EPUB or FB2 of at most 50 MiB.','выберите непустой файл EPUB или FB2 размером не более 50 МиБ.'],close:['Close','Закрыть'],bookmarkPassage:['Bookmark','Закладка'],deleteIllustrationConfirm:['Delete this illustration from the local library? If it was the paragraph’s only image in a saved illustration run, that run will list the paragraph as unfinished; regenerating it later needs your explicit approval and may use Codex allowance or charges. Copies saved outside the app are unchanged.','Удалить эту иллюстрацию из локальной библиотеки? Если это было единственное изображение абзаца в сохранённой задаче, задача отметит абзац как незавершённый; повторное создание потребует вашего явного согласия и может расходовать лимит или средства Codex. Копии за пределами приложения не изменятся.'],deleteBookTitle:['Delete book?','Удалить книгу?'],deleteGenerationTitle:['Delete generation?','Удалить озвучку?'],deleteExportTitle:['Delete saved export?','Удалить сохранённый экспорт?'],deleteExportMessage:['Delete this saved export from the local library? Copies saved outside the app are unchanged.','Удалить этот экспорт из локальной библиотеки? Копии за пределами приложения не изменятся.'],deleteIllustrationTitle:['Delete illustration?','Удалить иллюстрацию?'],removeBookmarkTitle:['Remove bookmark and note?','Удалить закладку и заметку?'],renameGenerationTitle:['Rename generation','Переименовать озвучку'],generationName:['Generation name','Название озвучки'],stopBeforeDelete:['Wait for the running illustration preparation to finish before deleting this paragraph’s last image; deleting or stopping now would discard a paid request.','Дождитесь завершения текущей подготовки иллюстраций, прежде чем удалять последнее изображение этого абзаца: удаление или остановка сейчас отбросит платный запрос.'],imageMissing:['Image unavailable','Изображение недоступно'],
  reviewReady:['Voices assigned. Review the cast, then start narration.','Голоса назначены. Проверьте роли и запустите озвучку.'],reviewCast:['Review cast','Проверить роли'],startNarration:['Start narration','Начать озвучку'],startingNarration:['Starting narration','Запуск озвучки'],updatingCast:['Updating cast','Изменение ролей'],castReviewSetting:['Stop for cast review before narration','Останавливаться для проверки ролей перед озвучкой'],castReviewHelp:['After voices are assigned, narration waits until you start it in Characters & narration.','После назначения голосов озвучка ждёт, пока вы не запустите её в разделе «Персонажи и озвучка».'],reconciling:['Matching character names','Сопоставление имён персонажей'],goToPassage:['Go to passage','Перейти к отрывку'],newTake:['New take: synthesize this passage again, even without other changes','Новый дубль: озвучить отрывок заново, даже без других изменений'],unresolvedNarrator:['Unresolved speaker · narrator voice','Говорящий не определён · голос рассказчика'],cancelExport:['Cancel export','Отменить экспорт'],alignmentUnavailable:['Not available: the pack has no word aligner or the aligner module is not installed.','Недоступно: в комплекте нет модуля выравнивания по словам или он не установлен.'],renameCharacter:['Rename character','Переименовать персонажа'],characterLabel:['Character name','Имя персонажа'],mergeCharactersTitle:['Merge characters?','Объединить персонажей?'],merge:['Merge','Объединить'],
  failedPassage:['Could not be synthesized. Correct its spoken text, then resume.','Не удалось озвучить. Исправьте произносимый текст и продолжите озвучку.'],degradedPassage:['Speaker analysis failed; held for review.','Не удалось определить говорящих; отрывок ожидает проверки.'],codexMissing:['Codex CLI was not found. Install it and sign in, then reopen this panel.','Codex CLI не найден. Установите его, войдите в учётную запись и снова откройте эту панель.'],
  heldPassage:['Held for speaker review. It is not part of the finished audiobook until you confirm or correct it.','Ожидает проверки говорящего. Не войдёт в готовую аудиокнигу, пока вы его не подтвердите или не исправите.'],reviewHolds:['Review held passages','Проверить удержанные отрывки'],confirmSpeaker:['Confirm speaker','Подтвердить говорящего'],confirmingSpeaker:['Confirming speaker','Подтверждение говорящего'],confirmSpeakerHelp:['Accepts the shown speaker for this held passage in a new corrected version.','Принимает показанного говорящего для этого удержанного отрывка в новой исправленной версии.'],confirmSpeakerUnknown:['An unresolved speaker cannot be confirmed. Choose the speaker and save a corrected version.','Неопределённого говорящего нельзя подтвердить. Выберите говорящего и сохраните исправленную версию.'],confirmSpeakerChanged:['Save the corrected version to apply your changes; confirming accepts only the shown speaker.','Чтобы применить изменения, сохраните исправленную версию: подтверждение принимает только показанного говорящего.'],analysisParts:['Parts of this sentence','Части предложения'],partSpeaker:['Speaker for part','Говорящий части'],confirmPresentation:['Confirm presentation voice','Подтвердить голос подачи'],confirmingPresentation:['Confirming presentation voice','Подтверждение голоса подачи'],presentationReview:['Presentation voice to review','Голос подачи на проверку'],verdictsSeparate:['Confirming the speaker does not approve the presentation voice, and confirming the presentation voice does not confirm the speaker.','Подтверждение говорящего не утверждает голос подачи, а подтверждение голоса подачи не подтверждает говорящего.'],notHeld:['This passage is no longer held for review. Reopen it to see its current state.','Этот отрывок больше не ожидает проверки. Откройте его снова, чтобы увидеть текущее состояние.'],confirmPart:['Confirm part','Подтвердить часть'],partVerdictHelp:['Each part gets its own verdict. Confirm a part to accept its shown speaker, or choose another speaker and save a corrected version. Changing a part speaker regenerates this passage.','Каждая часть получает своё решение. Подтвердите часть, чтобы принять показанного говорящего, или выберите другого и сохраните исправленную версию. Смена говорящего части заново озвучит этот отрывок.'],confirmSpeakerMixed:['In this mixed sentence, Confirm speaker accepts only the parts spoken by the shown speaker. Other parts stay held until each gets its own verdict.','В этом смешанном предложении «Подтвердить говорящего» принимает только части показанного говорящего. Остальные части остаются удержанными, пока каждая не получит своё решение.'],partAndWholeMixed:['Part speakers and whole-passage changes are saved separately. Undo one kind of change, save, then make the other.','Говорящие частей и изменения всего отрывка сохраняются отдельно. Отмените один вид изменений, сохраните, затем внесите другой.'],mixedSpokenLocked:['This sentence changes speaker, narration or delivery inside it. Each part keeps its own voice, so its spoken text cannot be edited as one passage.','Внутри этого предложения меняется говорящий, речь автора или подача. Каждая часть сохраняет свой голос, поэтому произносимый текст нельзя изменить целиком.'],approvalKeepsHolds:['Starting narration does not release speaker holds. Held passages stay out of the finished audiobook until you confirm or correct each one.','Запуск озвучки не снимает удержание говорящих. Удержанные отрывки не войдут в готовую аудиокнигу, пока вы не подтвердите или не исправите каждый из них.'],heldGeneration:['Preparation finished, but some passages are held for speaker review. Confirm or correct each one, then resume preparation.','Подготовка завершена, но часть отрывков ожидает проверки говорящего. Подтвердите или исправьте каждый из них, затем продолжите озвучку.'],exportHeld:['A complete audiobook cannot be exported while passages are held for speaker review. Confirm or correct them in Characters & narration.','Полную аудиокнигу нельзя экспортировать, пока отрывки ожидают проверки говорящего. Подтвердите или исправьте их в разделе «Персонажи и озвучка».'],
  characterAnalysis:['Character analysis','Анализ персонажей'],analysisAuto:['Best available CLI','Лучший доступный CLI'],analysisLocal:['Local model · checks twice','Локальная модель · двойная проверка'],analysisCodex:['GPT via Codex CLI','GPT через Codex CLI'],analysisClaude:['Claude via Claude Code CLI','Claude через Claude Code CLI'],characterAnalysisHelp:['Best available uses Codex, then Claude, then the local model. The local model checks each speaker twice and leaves its speaker suggestions for review. CLI modes use your existing signed-in subscription. The choice is pinned to new generations.','Лучший доступный вариант использует Codex, затем Claude, затем локальную модель. Локальная модель проверяет каждого говорящего дважды и оставляет свои варианты на проверку. Режимы CLI используют вашу подписку, в которую уже выполнен вход. Выбор закрепляется за новыми озвучками.'],tieBreaker:['Hard-case speaker check','Проверка сложных реплик'],tieBreakerNone:['Local result + review flags','Локальный результат + отметки для проверки'],tieBreakerCodex:['Codex CLI tie-breaker','Решающая проверка через Codex CLI'],tieBreakerClaude:['Claude CLI tie-breaker','Решающая проверка через Claude CLI'],tieBreakerHelp:['Only difficult speaker spans are sent to the selected tie-breaker. The local result and reason are preserved for review.','Выбранной решающей проверке отправляются только сложные фрагменты. Локальный результат и причина сохраняются для проверки.'],unresolvedPolicy:['Unresolved speakers','Неопределённые говорящие'],unresolvedHold:['Keep marked gaps','Оставлять отмеченные пропуски'],unresolvedPreview:['Narrator preview with warning','Предпросмотр голосом рассказчика с предупреждением'],unresolvedPolicyHelp:['Marked gaps hold the unresolved passage and its direct neighbours while the rest of the book is prepared. A narrator preview is only a marked preview: the passages stay held and a complete export stays blocked until you confirm or correct them.','Отмеченные пропуски удерживают неопределённый отрывок и соседние с ним, пока готовится остальная книга. Голос рассказчика — лишь отмеченный предпросмотр: отрывки остаются удержанными, а полный экспорт заблокирован, пока вы их не подтвердите или не исправите.'],
  recommendationsEyebrow:['FROM FANTLAB READERS','ПО ОЦЕНКАМ ЧИТАТЕЛЕЙ FANTLAB'],recommendationsTitle:['What to narrate next','Что озвучить дальше'],recommendationsText:['Pick the genres you enjoy. Books in your library count as examples too.','Отметьте любимые жанры. Книги из вашей библиотеки тоже учитываются как примеры.'],
  recommendationsGenres:['Genres','Жанры'],recommendationsConsentTitle:['Suggestions come from FantLab','Подсказки берутся с FantLab'],recommendationsConsent:['Turning this on sends the titles, series, authors and ISBNs of books in your library and the genres you pick to FantLab (fantlab.ru), and loads covers from there. Book text and audio stay on this computer.','При включении названия, серии, авторы и ISBN книг из вашей библиотеки и выбранные жанры отправляются на FantLab (fantlab.ru), а обложки загружаются оттуда же. Текст книг и аудио остаются на этом компьютере.'],
  recommendationsEnable:['Show suggestions','Показать подсказки'],recommendationsRetry:['Try again','Повторить'],recommendationsLoading:['Loading suggestions…','Загружаем подсказки…'],recommendationsEmpty:['Pick a genre or add a book to get suggestions.','Выберите жанр или добавьте книгу, чтобы получить подсказки.'],
  recommendationsUnmatched:['Your books were not found on FantLab. Pick a few genres instead.','Ваши книги не найдены на FantLab. Выберите несколько жанров.'],recommendationsFailed:['Could not load suggestions from FantLab.','Не удалось загрузить подсказки с FantLab.'],recommendationsPartial:['Some FantLab requests failed; showing what loaded.','Часть запросов к FantLab не удалась; показано то, что загрузилось.'],
  recommendationsMore:['Show more','Показать ещё'],recommendationsLess:['Show fewer','Свернуть'],recommendationsCopyLink:['Copy FantLab link','Скопировать ссылку на FantLab'],recommendationsCopied:['Link copied','Ссылка скопирована'],recommendationsSimilar:['Similar to','Похоже на'],recommendationsInGenre:['Popular in','Популярно в жанре'],
  recommendationsRating:['FantLab rating','Рейтинг FantLab'],recommendationsSetting:['Suggest books to narrate from FantLab','Подсказывать книги для озвучки с FantLab'],recommendationsSettingTitle:['Online book suggestions','Сетевые подсказки книг'],
} as const;
export type Message=keyof typeof messages;
export function translator(language:'en'|'ru'){return (key:Message):string=>messages[key][language==='ru'?1:0];}
const statuses:Record<string,Message>={ready:'ready',paused:'paused',failed:'failed',pending:'pending',queued:'queued',analyzing:'analyzing',casting:'casting',rendering:'rendering',aligning:'aligning',review:'review',held:'held'};
export function statusLabel(language:'en'|'ru',status:string):string{return translator(language)(statuses[status]||'pending');}

export function operationMessage(reason:unknown,language:'en'|'ru'):string{
  if(!(reason instanceof Error))return localMessage(language,String(reason));
  if('code' in reason&&reason.code==='illustration_run_active')return messages.stopBeforeDelete[language==='ru'?1:0];
  if('code' in reason&&reason.code==='unknown_speaker')return messages.confirmSpeakerUnknown[language==='ru'?1:0];
  if('code' in reason&&reason.code==='not_held')return messages.notHeld[language==='ru'?1:0];
  const details='details' in reason&&reason.details&&typeof reason.details==='object'?reason.details:{};
  const field=(key:string):unknown=>Reflect.get(details,key);
  const text=(key:string)=>{const value=field(key);return typeof value==='string'?value:'';};
  if('code' in reason&&reason.code==='opening_language_required')return language==='ru'
    ?`Для этой книги нужна заставка Dogma на языке книги (${text('language')||'язык не указан'}). Сейчас доступны русский и английский. Добавьте утверждённую заставку на нужном языке или уточните язык книги.`
    :`This book requires a matching-language Dogma opening (${text('language')||'unknown language'}). Russian and English are available. Add an approved opening in the book’s language or correct its language metadata.`;
  if('code' in reason&&reason.code==='illustration_paragraph_too_long'&&language==='ru'){const limit=field('limit');return `Абзац «${text('opening')}…» в главе «${text('chapter')}» превышает лимит иллюстраций в ${(typeof limit==='number'?limit:16000).toLocaleString('ru-RU')} символов. Текст не сокращался.`;}
  if(!('code' in reason)||reason.code!=='invalid_book_xml')return localMessage(language,reason.message);
  const document=text('document');
  const lineValue=field('line');
  const line=typeof lineValue==='number'?lineValue:null;
  if(language==='ru'){const where=line?` (строка ${line})`:'';return document?`Документ книги ${document} содержит некорректный XML${where}.`:`Книга содержит некорректный XML${where}.`;}
  const where=line?` at line ${line}`:'';
  return document?`Book document ${document} is not well-formed XML${where}.`:`The book is not well-formed XML${where}.`;
}

export function jobElsewhere(language:'en'|'ru',kind:'audio'|'illustrations',title:string):string{
  const name=title?(language==='ru'?`«${title}»`:`“${title}”`):'';
  if(kind==='audio')return language==='ru'?`Сейчас готовится аудио${name?` для ${name}`:''}. Приостановите эту подготовку или дождитесь её завершения, чтобы начать или продолжить другую.`:`Audio${name?` for ${name}`:''} is being prepared. Pause it or wait until it finishes before starting or resuming another generation.`;
  return language==='ru'?`Сейчас готовятся иллюстрации${name?` для ${name}`:''}. Остановите их или дождитесь завершения, чтобы начать другую подготовку.`:`Illustrations${name?` for ${name}`:''} are being prepared. Stop them or wait until they finish before starting another run.`;
}

const nouns={
  books:[['book','books'],['книга','книги','книг']],
  chapters:[['chapter','chapters'],['глава','главы','глав']],
  sections:[['section','sections'],['раздел','раздела','разделов']],
  characters:[['character','characters'],['символ','символа','символов']],
  narrationCharacters:[['narration character','narration characters'],['символ для озвучки','символа для озвучки','символов для озвучки']],
  segments:[['segment','segments'],['фрагмент','фрагмента','фрагментов']],
  passages:[['passage','passages'],['отрывок','отрывка','отрывков']],
  baseSegments:[['base segment','base segments'],['исходный фрагмент','исходных фрагмента','исходных фрагментов']],
  ratings:[['rating','ratings'],['оценка','оценки','оценок']],
} as const;
export function preview(text:string,limit=160):string{const clean=text.replace(/\s+/g,' ').trim();return clean.length>limit?clean.slice(0,limit-1).trimEnd()+'…':clean;}
export function locale(language:'en'|'ru'){return language==='ru'?'ru-RU':'en-US';}
export function count(language:'en'|'ru',value:number,noun:keyof typeof nouns):string{
  const [english,russian]=nouns[noun];
  const tens=value%100,units=value%10;
  const form=language==='ru'?russian[units===1&&tens!==11?0:units>=2&&units<=4&&(tens<12||tens>14)?1:2]:english[value===1?0:1];
  return `${value.toLocaleString(locale(language))} ${form}`;
}
export function importNotice(language:'en'|'ru',added:number,existing:number):string{
  const parts=language==='ru'?[added?`Импортировано: ${count('ru',added,'books')}. Сохранено в локальной библиотеке.`:'',existing?`Уже в библиотеке: ${count('ru',existing,'books')}.`:'']
    :[added?`${count('en',added,'books')} imported. Saved in your local library.`:'',existing?`${count('en',existing,'books')} already in your library.`:''];
  return parts.filter(Boolean).join(' ');
}
const deliveryNames:Record<string,[string,string]>={neutral:['Neutral','Нейтрально'],soft:['Soft','Мягко'],whisper:['Whisper','Шёпотом'],firm:['Firm','Твёрдо'],excited:['Excited','Взволнованно'],sad:['Sad','Грустно'],angry:['Angry','Сердито'],questioning:['Questioning','Вопросительно'],enumeration:['Enumeration','Перечисление'],stunned:['Stunned','Ошеломлённо']};
export function characterName(language:'en'|'ru',character:{id:string;name:string}):string{return character.id==='narrator'?(language==='ru'?'Рассказчик':'Narrator'):character.name;}
export function passageProgress(language:'en'|'ru',ready:number,total:number,state:'prepared'|'saved'):string{
  if(language==='ru')return `${state==='prepared'?'Готово':'Сохранено'} отрывков: ${ready.toLocaleString('ru-RU')}/${total.toLocaleString('ru-RU')}`;
  return `${ready}/${count('en',total,'passages')} ${state}`;
}
export function deliveryLabel(language:'en'|'ru',value:string):string{return deliveryNames[value]?.[language==='ru'?1:0]??value;}
const backendMessages:Record<string,string>={
  'This passage is not ready. Resume preparation to continue.':'Этот отрывок ещё не готов. Продолжите подготовку.',
  'This passage is held for review. Review it in Characters & narration to continue.':'Этот отрывок ожидает проверки. Проверьте его в разделе «Персонажи и озвучка», чтобы продолжить.',
  'This passage is not ready. Review the cast and start narration to continue.':'Этот отрывок ещё не готов. Проверьте роли и запустите озвучку.',
  'Clipboard is not available.':'Буфер обмена недоступен.',
  'Preparation failed. Resume preparation to retry.':'Подготовка не удалась. Продолжите подготовку, чтобы повторить.',
  'Loading saved audio timed out. Press Play to retry, or check and repair saved audio.':'Сохранённое аудио не загрузилось вовремя. Нажмите «Слушать», чтобы повторить, или проверьте и восстановите аудио.',
  'The saved audio could not be played. Press Play to retry.':'Не удалось воспроизвести сохранённое аудио. Нажмите «Слушать», чтобы повторить.',
  'A saved audio segment is missing or cannot be opened.':'Сохранённый фрагмент аудио отсутствует или не открывается.',
  'A saved audio segment is missing.':'Сохранённый фрагмент аудио отсутствует.',
  'Completed audio not found.':'Готовое аудио не найдено.',
  'Saved audio repair is incomplete. Resume preparation before playback.':'Восстановление аудио не завершено. Продолжите подготовку перед прослушиванием.',
  'Saved audio repair is incomplete. Resume preparation before exporting.':'Восстановление аудио не завершено. Продолжите подготовку перед экспортом.',
  'Preparation was interrupted. Resume to reuse completed audio.':'Подготовка была прервана. Продолжите её — готовое аудио будет использовано повторно.',
  'Local preparation could not start. Resume to retry.':'Локальная подготовка не запустилась. Продолжите, чтобы повторить.',
  'Unresolved speaker; narrator voice is used.':'Говорящий не определён; используется голос рассказчика.',
  'Word alignment is unavailable; passage subtitles are retained.':'Выравнивание по словам недоступно; субтитры по отрывкам сохранены.',
  'Playback offset exceeds the saved segment.':'Позиция выходит за пределы сохранённого фрагмента.',
  'Selected folder does not contain a valid pack.json.':'В выбранной папке нет корректного pack.json.',
  'No local model pack is selected. Open Settings and choose a prepared pack.':'Локальный комплект моделей не выбран. Откройте настройки и выберите подготовленный комплект.',
  'Model pack changed. This generation is pinned to the original revision.':'Комплект моделей изменился. Эта озвучка привязана к исходной версии комплекта.',
  'Pack contents changed. Re-seal this pack as a new revision.':'Содержимое комплекта изменилось. Запечатайте его заново как новую версию.',
  'Not a valid EPUB archive.':'Файл не является корректным архивом EPUB.',
  'Only EPUB and FB2 are supported.':'Поддерживаются только EPUB и FB2.',
  'Bookmark note is too long.':'Заметка к закладке слишком длинная.',
  'Another book is being prepared. Pause it before starting this generation.':'Сейчас готовится другая книга. Приостановите её, чтобы начать эту озвучку.',
  'Another generation is active. Pause it before creating a new one.':'Другая озвучка уже готовится. Приостановите её, чтобы создать новую.',
  'Pause generation before deleting it.':'Приостановите озвучку перед удалением.',
  'Pause preparation before deleting a book.':'Приостановите подготовку перед удалением книги.',
  'Pause preparation before checking and repairing saved audio.':'Приостановите подготовку перед проверкой и восстановлением аудио.',
  'Pause this generation before creating a corrected version.':'Приостановите озвучку перед созданием исправленной версии.',
  'Stop illustration preparation before deleting this book.':'Остановите подготовку иллюстраций перед удалением книги.',
  'Finish character analysis before making passage corrections.':'Дождитесь завершения анализа персонажей перед исправлением отрывков.',
  'Finish character analysis before repairing saved audio.':'Дождитесь завершения анализа персонажей перед восстановлением аудио.',
  'Every segment in the selected chapters must be ready before export.':'Перед экспортом все отрывки выбранных глав должны быть готовы.',
  'FFmpeg is missing from the local runtime.':'В локальной среде нет FFmpeg.',
  'FFprobe is missing; encoded audio cannot be verified before export.':'Нет FFprobe: закодированное аудио нельзя проверить перед экспортом.',
  'Select at least one valid chapter.':'Выберите хотя бы одну доступную главу.',
  'The selected chapters contain no narratable text.':'В выбранных главах нет текста для озвучки.',
  'The local application is closing.':'Приложение закрывается.',
  'Pause confirmation was interrupted or timed out.':'Подтверждение паузы прервано или не получено вовремя.',
  'An Images API request for this paragraph is still running. Wait for it to finish before requesting another image.':'Запрос изображения через Images API для этого абзаца ещё выполняется. Дождитесь его завершения, прежде чем запрашивать новое изображение.',
  'Book has no readable text.':'В книге нет текста для чтения.',
  'Book text exceeds two million characters.':'Текст книги превышает два миллиона символов.',
  'Text model context must be at least 8192 tokens.':'Контекст текстовой модели должен быть не меньше 8192 токенов.',
  'Model pack files changed after verification. Select the pack again in Settings.':'Файлы комплекта моделей изменились после проверки. Выберите комплект заново в настройках.',
  'The bundled inference runtime is missing. Reinstall the application.':'Встроенная среда озвучки отсутствует. Переустановите приложение.',
  'Voice families must be stable, whitespace-free identifiers.':'Семейства голосов должны быть постоянными идентификаторами без пробелов.',
  'A voice pack needs at least one character performance.':'В комплекте голосов нужен хотя бы один голос персонажа.',
  'Unresolved speaker; held for review.':'Говорящий не определён; отрывок ожидает проверки.',
  'Speaker analysis failed for this passage; held for review.':'Не удалось определить говорящих в этом отрывке; отрывок ожидает проверки.',
  'Multiple characters share one sentence; each part keeps its own voice.':'В одном предложении говорят несколько персонажей; каждая часть звучит своим голосом.',
  'Internal speech, narration, or delivery change retained for split review; parts are rendered separately.':'Внутри предложения меняются речь, авторский текст или подача; части сохранены для проверки разбиения и озвучиваются отдельно.',
  'Held until the speaker is reviewed.':'Ожидает проверки говорящего.',
  'Held next to a speaker-review gap.':'Ожидает: соседний отрывок требует проверки говорящего.',
  'Held until its presentation voice is reviewed.':'Ожидает проверки голоса подачи.',
  'Held: the speech model reached its generation limit for this passage. Shorten or correct its spoken text; the text was not clipped.':'Ожидает: модель речи достигла предела генерации для этого отрывка. Сократите или исправьте произносимый текст; текст не был обрезан.',
  'Unreviewed preview; this passage is still held for review.':'Непроверенный предпросмотр; отрывок по-прежнему ожидает проверки.',
  'Held: this sentence mixes voices, so its edited spoken text cannot be split into parts. Restore the source text or correct each part.':'Ожидает: в предложении несколько голосов, поэтому изменённый произносимый текст нельзя разделить на части. Верните исходный текст или исправьте каждую часть.',
  'Choose a speaker before confirming this passage.':'Выберите говорящего, прежде чем подтверждать этот отрывок.',
  'Only a held passage can be confirmed.':'Подтвердить можно только отрывок, ожидающий проверки.',
  'This passage changes voice inside the sentence; its spoken text cannot be edited as one passage.':'В этом отрывке голос меняется внутри предложения; его произносимый текст нельзя редактировать целиком.',
  'Held: the pinned model pack cannot perform the planned pace for this passage; it was not rendered at a default pace.':'Ожидает: закреплённый комплект моделей не может исполнить запланированный темп этого отрывка; он не озвучен с темпом по умолчанию.',
  'Speaker analysis failed for this passage; the narrator voice is used.':'Не удалось определить говорящих в этом отрывке; используется голос рассказчика.',
  'A passage could not be synthesized after a retry. Open the marked passage to correct its spoken text, then resume.':'Отрывок не удалось озвучить даже после повторной попытки. Откройте отмеченный отрывок, исправьте произносимый текст и продолжите озвучку.',
  'The selected device is not available for speech synthesis. Choose another device in Settings and resume.':'Выбранное устройство недоступно для синтеза речи. Выберите другое устройство в настройках и продолжите озвучку.',
  'Word alignment stopped because the local aligner exited; passage subtitles are kept.':'Выравнивание по словам остановлено: локальный модуль выравнивания завершил работу. Субтитры по отрывкам сохранены.',
  'Speech duration looks unusual for this passage. Use New take if it sounds wrong.':'Длительность речи в этом отрывке выглядит необычно. Если звучит неправильно, используйте «Новый дубль».',
  'Speech output is empty, invalid, silent, or too long.':'Синтезированная речь пустая, повреждённая, беззвучная или слишком длинная.',
  'Speech may have reached its generation cap; output was not accepted.':'Речь, возможно, достигла предела генерации; результат не принят.',
  'This model has no validated delivery control; neutral performance was requested.':'У этой модели нет проверенного управления подачей; запрошено нейтральное исполнение.',
  'Requested delivery has no reference variant; neutral reference was used.':'Для запрошенной подачи нет эталонного варианта; использован нейтральный эталон.',
  'Passage subtitles are retained.':'Субтитры по отрывкам сохранены.',
  'Alignment did not return a complete cue sequence.':'Выравнивание по словам вернуло неполную последовательность субтитров.',
  'Invalid alignment cue.':'Некорректный субтитр выравнивания по словам.',
  'Alignment timestamps or text are invalid.':'Метки времени или текст выравнивания по словам некорректны.',
  'Alignment cue starts outside the audio.':'Субтитр выравнивания по словам начинается за пределами аудио.',
  'Alignment text does not cover the spoken passage; using passage subtitles.':'Текст выравнивания по словам не совпадает с произносимым текстом отрывка; используются субтитры по отрывкам.',
  'Export cancelled. No incomplete output was published.':'Экспорт отменён. Незавершённый файл не сохранён.',
  'Secure connection to the image service failed (certificate check). No automatic paid retry was made.':'Не удалось установить защищённое соединение с сервисом изображений (проверка сертификата). Автоматического платного повтора не было.',
  'Image preparation failed. No automatic paid retry was made.':'Не удалось подготовить изображение. Автоматического платного повтора не было.',
  'This paragraph exceeds the 16,000-character illustration limit and was skipped. No text was truncated.':'Абзац превышает лимит иллюстраций в 16 000 символов и пропущен. Текст не сокращался.',
  'Local runtime is not running':'Локальная среда не запущена',
  'The book text encoding could not be read.':'Не удалось прочитать кодировку текста книги.',
  'The speech worker stopped twice while rendering a passage. Resume to try again.':'Процесс синтеза речи дважды остановился при озвучке отрывка. Продолжите озвучку, чтобы попробовать снова.',
  'Book markup exceeds the safety limit of 200,000 elements, 400,000 attributes or 400,000 text runs.':'Разметка книги превышает предел безопасности: 200 000 элементов, 400 000 атрибутов или 400 000 текстовых фрагментов.',
  'Some vector drawings were too large and were omitted.':'Некоторые векторные рисунки оказались слишком большими и пропущены.',
  'Cast changes are possible only while the generation waits for cast review.':'Изменять роли можно, только пока озвучка ожидает проверки ролей.',
  'Choose two different characters to merge.':'Выберите двух разных персонажей для объединения.',
  'This voice cannot be used for this character.':'Этот голос нельзя назначить этому персонажу.',
  'Choose a narrator voice of this model pack.':'Выберите голос рассказчика из этого комплекта моделей.',
  'Unknown character kind.':'Неизвестный тип персонажа.',
  'Missing speech model files.':'Отсутствуют файлы речевой модели.',
  'Bundled runtime components are from different builds. Reinstall the application.':'Встроенные компоненты среды относятся к разным сборкам. Переустановите приложение.',
  'Another export is in progress.':'Уже выполняется другой экспорт.',
  'The generation was deleted during export. No export was published.':'Озвучка была удалена во время экспорта. Экспорт не опубликован.',
  'Local inference process exited.':'Локальный процесс модели завершил работу.',
  'Local inference process is unavailable.':'Локальный процесс модели недоступен.',
  'Local inference process exited before completing its request.':'Локальный процесс модели завершил работу, не выполнив запрос.',
};
const backendKeys=Object.keys(backendMessages).sort((a,b)=>b.length-a.length);
const patternMessages:Array<[RegExp,string]>=[[/(\d+) held passage\(s\) still need review\. A complete export was not made; held text is never omitted or narrated as accepted audio\./y,'Отрывков, ожидающих проверки: $1. Полный экспорт не создан: ожидающий текст никогда не пропускается и не озвучивается как принятое аудио.'],[/Expected nonempty text of at most (\d+) characters\./y,'Ожидается непустой текст длиной не более $1 символов.'],[/Local inference exceeded its (\d+)-second operation limit\./y,'Локальный процесс модели превысил лимит времени операции ($1 с).'],[/EPUB entry (.+?) is damaged and cannot be read\./y,'Файл $1 в архиве EPUB повреждён и не может быть прочитан.'],[/The text encoding of book document (.+?) could not be read\./y,'Не удалось прочитать кодировку текста документа книги $1.'],[/Book document (.+?) is not well-formed XML; it was read in recovery mode and all of its text was kept\./y,'Документ книги $1 содержит некорректный XML; он прочитан в режиме восстановления, весь текст сохранён.']];
const processOutput=' Process output: ';
const partEnds=(text:string,english:string,end:number)=>end===text.length||/[.!?;:]$/.test(english)&&/\s/.test(text[end]);
function knownPart(text:string,at:number):[number,string]|null{
  const key=backendKeys.find(key=>text.startsWith(key,at)&&partEnds(text,key,at+key.length));
  if(key)return [key.length,backendMessages[key]];
  for(const [expression,russian] of patternMessages){
    expression.lastIndex=at;const match=expression.exec(text);
    if(match&&partEnds(text,match[0],at+match[0].length))return [match[0].length,russian.replace(/\$(\d)/g,(_,group:string)=>match[Number(group)]??'')];
  }
  return null;
}
function joinedMessages(message:string):string|null{
  let output='',copied=0;
  for(let at=0;at<message.length;){
    const known=knownPart(message,at);
    if(known){output+=message.slice(copied,at)+known[1];at+=known[0];copied=at;continue;}
    while(at<message.length&&!/\s/.test(message[at]))at++;
    at++;
  }
  return copied?output+message.slice(copied):null;
}
const prefixedMessages:Array<[string,string]>=[['Could not save playback position: ','Не удалось сохранить место прослушивания: '],['Preparation could not start: ','Не удалось начать подготовку: '],['Runtime cleanup failed: ','Не удалось завершить работу среды: '],['FFmpeg export failed: ','Ошибка экспорта FFmpeg: ']];
export function localMessage(language:'en'|'ru',message:string):string{
  const text=message.replace(/^(?:AppError: )+/,'');
  if(language!=='ru'||!text)return text;
  const prefix=prefixedMessages.find(([english])=>text.startsWith(english));
  if(prefix)return prefix[1]+localMessage(language,text.slice(prefix[0].length));
  const output=text.indexOf(processOutput);
  if(output>0)return `${localMessage(language,text.slice(0,output))} Вывод процесса: ${text.slice(output+processOutput.length)}`;
  return joinedMessages(text)??text;
}
export const playerMessage=localMessage;
export function passageNote(language:'en'|'ru',segment?:{status:string;warning:string}|null):{text:string;detail:string}{
  const warning=localMessage(language,segment?.warning||'');
  return segment?.status==='failed'?{text:translator(language)('failedPassage'),detail:warning}:segment?.status==='review'?{text:translator(language)('heldPassage'),detail:warning}:{text:warning,detail:''};
}
const blockKinds:Record<string,[string,string]>={figure:['Figure','Рисунок'],equation:['Equation','Формула'],table:['Table','Таблица'],separator:['Separator','Разделитель'],note:['Note','Примечание'],heading:['Heading','Заголовок'],paragraph:['Passage','Отрывок']};
export function blockKindLabel(language:'en'|'ru',kind:string):string{return blockKinds[kind]?.[language==='ru'?1:0]??kind;}
