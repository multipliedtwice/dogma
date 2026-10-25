export function PreparationDisclosure({local,ru}:{local:boolean;ru:boolean}){
  return <p className="helper" role="note">{local
    ?(ru?'Подготовка русского текста работает без отправки книги в сеть. Неоднозначные ударения и е/ё останутся на разборе перед озвучкой.':'Russian preparation stays offline. Unresolved stress and е/ё choices pause narration for review.')
    :(ru?'При подготовке русского текста предложения с неоднозначным ударением отправляются Claude Opus 5.5, а с неоднозначным е/ё — GPT-6 Luna. Сохранённые вами исправления имеют приоритет.':'For Russian narration, sentences with disputed stress are sent to Claude Opus 5.5; ambiguous е/ё cases go to GPT-6 Luna. Your saved corrections take precedence.')}</p>;
}
