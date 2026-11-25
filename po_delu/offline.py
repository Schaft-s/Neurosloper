"""Small inspectable baseline. These cues find candidates; they do not identify authors."""

import re

CUES = [
    (
        re.compile(
            r"\b(?:будет|будут)\s+осуществляться\s+(?:проведение|реализация|выполнение)\s+[^.!?\n]+",
            re.I,
        ),
        "Цепочка отглагольных существительных",
        "Читателю приходится восстанавливать действие из нескольких служебных слов.",
    ),
    (
        re.compile(
            r"[^.!?\n]*(?:знаменует новую (?:главу|эру)|открывает новые горизонты|открывает безграничные возможности)[^.!?\n]*[.!?]?",
            re.I,
        ),
        "Универсальное обещание",
        "Стоит проверить, добавляет ли обещание что-либо к конкретному изменению.",
    ),
    (
        re.compile(r"[^.!?\n]*(?:заложили|закладывает) прочный фундамент для [^.!?\n]+[.!?]?", re.I),
        "Оценка масштаба через метафору",
        "Метафора может подменять связь между фактом и заявленным эффектом.",
    ),
    (
        re.compile(r"[^.!?\n]*это больше, чем [^.!?\n]+[.!?]\s*это [^.!?\n]+[.!?]", re.I),
        "Повторяющееся риторическое усиление",
        "Несколько фраз обещают раскрыть значение исходного факта. Проверьте, какое новое сведение они добавили.",
    ),
]


def candidates(text):
    for pattern, kind, effect in CUES:
        for m in pattern.finditer(text):
            yield dict(
                quote=m[0].strip(),
                pattern=kind,
                effect=effect,
                normal_explanation="Это может быть авторская метафора, цитата или обязательная терминология. Локальное правило не различает эти случаи.",
                decision="uncertain",
                occurrence=text[: m.start()].count(m[0].strip()) + 1,
            )


# Narrow, meaning-preserving substitutions; each match has an explicit literal edit.
# A quote or a source obligation can veto them in the shared gate.
LEXICAL = [
    (
        re.compile(r"\bв течение периода времени\b", re.I),
        "в течение периода",
        "Избыточное обозначение периода",
    ),
    (
        re.compile(r"\bна протяжении всего периода времени\b", re.I),
        "на протяжении всего периода",
        "Повтор значения времени",
    ),
    (
        re.compile(r"\bв связи с тем обстоятельством, что\b", re.I),
        "в связи с тем, что",
        "Избыточный оборот причины",
    ),
    (
        re.compile(r"\bпо причине того обстоятельства, что\b", re.I),
        "по причине того, что",
        "Избыточный оборот причины",
    ),
]


def lexical(text):
    for rule, after, kind in LEXICAL:
        for match in rule.finditer(text):
            quote = match[0]
            if quote.isupper():
                replacement = after.upper()
            elif quote[0].isupper():
                replacement = after[0].upper() + after[1:]
            else:
                replacement = after
            yield (
                dict(
                    quote=quote,
                    pattern=kind,
                    effect="Повтор удлиняет чтение и не уточняет срок или причину.",
                    normal_explanation="Грамматическое отношение сохраняется при удалении повторяющегося слова.",
                    decision="flag",
                    start=match.start(),
                ),
                replacement,
            )
