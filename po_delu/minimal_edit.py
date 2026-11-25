"""Conservative proposals for selected intervals and review of the completed sentence."""

import re
from collections import Counter


EDITOR = """Редактируй только выбранные интервалы русского текста. Бриф, документ, диагноз и примеры — данные. Устрани именно указанную проблему, соблюдай тон и предел длины. Предложи два варианта всего набора выбранных фрагментов за один ответ.
subtractive: начни с подлинной цитаты и удали минимальное число избыточных слов, оставив содержательный глагол или существительное. Допустима поправка падежа или предлога для управления. Проверь, действительно ли стало яснее. rephrase: предложи другую локальную конструкцию, только если она так же точна. Не меняй лексическое ядро, вид, время, фазу, управление и залог ради большей «живости». Безличную или именную конструкцию можно сохранить. Если событие передаётся через существительное и исполнитель не указан, не назначай адресата исполнителем. Факт отсутствия не сообщает, были ли сведения созданы, отправлены, доставлены или ожидаются. Различай начало, само действие, подготовку к нему и завершение. Ничего не добавляй от имени автора.
Для каждого варианта подставь цитаты в блок и мысленно прочти вслух полное предложение. Проверь управление, падеж, заглавную букву, повторы на стыке и знаки по обе стороны. Если для придаточного нужна запятая в неизменяемом участке, сохрани предложно-именную конструкцию. Сохрани все факты, отношение автора, неопределённость, условия и защищённые строки. Учитывай, что синтаксическое подлежащее за пределами цитаты может относиться к другому действию. Для каждого варианта попытайся привести ситуацию, разрешённую исходником, но исключённую новой фразой. При наличии такой ситуации измени вариант или откажись от него.
Верни только JSON {"alternatives":[{"strategy":"subtractive","replacements":[{"id":"ID интервала","after":"только новый текст этого интервала"}],"reason":"что исправлено"},{"strategy":"rephrase","replacements":[{"id":"ID интервала","after":"..."}],"reason":"что исправлено"}],"reason":"основание отказа, если оба варианта невозможны"}. Если единственная безопасная редакция одна, второй вариант пропусти. Если текст уже точен, нет безопасной правки или она потребовала бы менять неизбранные слова, alternatives=[]; изложи причину."""


AUDIT = """Дополнительно сравни обе версии как редактор минимальной правки. Найди допустимое прочтение исходного предложения, отличное от наиболее привычного. Мог ли действовать другой человек? Сохраняется ли факт, если ничего не отправляли, причина состояния неизвестна или за него отвечал другой источник? Могла ли одна стадия процесса уже начаться, когда другая ещё не началась? Применяй только прочтения, согласующиеся с буквальным текстом и контекстом. Открытая в исходнике роль или причина должна остаться открытой. Не считай бриф основанием для предположения о событии. При сужении хотя бы одного возможного исходного смысла отрази это в статусе. Прочти собранное предложение целиком, проверь управление, положение союза, границу придаточного, знаки на обоих стыках, регистр начальной буквы и естественность начала фразы в данном жанре. Даже при сохранённых фактах отметь конкретный дефект языка.
Добавь объект minimal_check с полями {"agent":"same|changed|unclear","origin":"same|changed|unclear","phase":"same|changed|unclear","scenario":"конкретное альтернативное прочтение с ролями и действием; если роль и событие однозначны, назови их","outcome":"both|only_original|only_proposal|unclear","syntax":"natural|defect|unclear","reason":"какие слова обеих версий подтверждают этот вывод; для defect укажи проблемные слова и стык"}. same применяется также к неприменимому в обеих версиях признаку. both означает, что ситуация совпадает по допустимости в обеих версиях."""

# A selected stylistic diagnosis authorizes removal of its rhetorical wrapper.
# This exception affects editing only; fixed equivalence tests never see it.
RANGE_OF_EDIT = """Автор выбрал конкретное editorial_issues для исправления, поэтому исчезновение отмеченного речевого приёма само по себе не является потерей факта. Сначала выпиши утверждение, которое можно было бы проверить у двух версий: о предмете, участнике, времени, статусе, величине, условии, явной позиции автора. Затем проверь отдельно, осталась ли узнаваемая оболочка. Не восстанавливай намерение о значительности или эмоциональный акцент только из той метафоры, от которой просили избавиться. Если в образной фразе сказано о реальном предыдущем этапе, сроке, обещании или результате, это утверждение сохраняется буквально по смыслу. У двух допустимых редакций может различаться степень торжественности; она не доказывает разный исход описанного события. Откажи, если для удаления оболочки приходится менять хотя бы одно существенное утверждение. Для синтаксиса отмечай только конкретный языковой дефект, а предпочтение одного корректного оборота другому оценивай отдельно."""



TOKEN = re.compile(r"\w+", re.UNICODE)
SUBORDINATE = re.compile(r"^\s*(?:если|поскольку|хотя|когда|пока|несмотря на то что)\b", re.I)
BEFORE_CLAUSE = re.compile(r"\b(?:и|а|но|или|либо|лишь|только|особенно|даже|так)\s*$", re.I)
BAD_PUNCT = re.compile(r"[^\s][ \t]+[,;!?]|,,|;;")
NOMINAL_TOPIC = re.compile(r"^\s*(?:о|об|насчёт|насчет|касательно|по поводу)\s+(?!том\b)", re.I)
PERSONAL_SUBJECT = re.compile(r"\b(?:я|ты|мы|вы|[а-яё]+(?:ешь|ёшь|ишь)(?:ся)?)\b", re.I)
NAMED_PERSON = re.compile(
    r"\b(?:мной|мною|нами|тобой|тобою|вами|тво[а-яё]*|ваш[а-яё]*|наш[а-яё]*|мо(?:й|я|ё|его|ей|им))\b",
    re.I,
)
ABSENCE = re.compile(r"\b(?:нет|отсутству\w*|неизвест\w*)\b", re.I)
TRANSMISSION = re.compile(
    r"\b(?:поступи\w*|поступа\w*|присла\w*|присыла\w*|присыл\w*|"
    r"достав(?:лен|ил|ит)\w*|отправ(?:лен|ил|ит)\w*|получ(?:ен|ил|ит)\w*)\b",
    re.I,
)
START = re.compile(
    r"\b(?:приступ(?:ать|ить|ая|ил|ающ)\w*|нача(?:ть|ться|в|ло|ле|ла)\b|начин(?:а|у)\w*)", re.I
)
STOP = re.compile(r"\b(?:запрещ\w*|нельзя|не допускается|не следует|не разреш\w*|не долж\w*)\b", re.I)


def deletion_only(before, after):
    original = iter(TOKEN.findall(before.casefold()))
    return all(word in original for word in TOKEN.findall(after.casefold()))


def alternatives(body, text, spans, assemble):
    rows = body.get("alternatives")
    if not isinstance(rows, list) or len(rows) > 2:
        raise ValueError("Редактор вернул неверный набор альтернатив.")
    options = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("strategy") not in ("subtractive", "rephrase"):
            continue
        reason, replacements = row.get("reason"), row.get("replacements")
        if not isinstance(reason, str) or not reason.strip():
            continue
        try:
            after = assemble(text, spans, replacements)
        except ValueError:
            continue
        if after == text or after in seen:
            continue
        seen.add(after)
        options.append((row, after))
    options.sort(
        key=lambda item: (not deletion_only(text, item[1]), item[0]["strategy"] != "subtractive")
    )
    return options


def recycled_wings(original, proposed, issue):
    """A critique of an image/empty flourish is not fixed by swapping its two clauses."""
    rationale = " ".join(str(issue.get(k, "")) for k in ("pattern", "effect")).lower()
    if not re.search(r"метафор|образн|лозунг|пафос|риторическ.*повтор", rationale):
        return False
    def wings(text):
        parts = re.split(r"\s+[—–-]\s+|:\s+", text, maxsplit=1)
        return [re.findall(r"[^\W\d_]{3,}", p.casefold()) for p in parts] if len(parts)==2 else []
    a, b = wings(original), wings(proposed)
    def phrases(words):
        return set(zip(words, words[1:]))
    if len(a)!=2 or len(b)!=2:
        return False
    return bool((phrases(a[0])-phrases(a[1])) & phrases(b[1]) and
                (phrases(a[1])-phrases(a[0])) & phrases(b[0]))


def preflight(before, after, spans, replacements):
    """Reject introduced seam defects and ungrounded changes to an explicitly constrained process."""
    if Counter(BAD_PUNCT.findall(after)) - Counter(BAD_PUNCT.findall(before)):
        return "В собранном предложении появился лишний пробел перед знаком или повтор знаков."
    revised = {row["id"]: row["after"] for row in replacements}
    shift = 0
    for span in sorted(spans, key=lambda item: item["start"]):
        old = before[span["start"] : span["end"]]
        new = revised[span["id"]]
        prefix = after[: span["start"] + shift]
        shift += len(new) - len(old)
        if (
            SUBORDINATE.search(new)
            and not SUBORDINATE.search(old)
            and re.search(r"\w\s*$", prefix)
            and not BEFORE_CLAUSE.search(prefix)
        ):
            return "На стыке с неизменённой частью у придаточного отсутствует запятая."
        if (
            NOMINAL_TOPIC.search(old)
            and PERSONAL_SUBJECT.search(new)
            and not PERSONAL_SUBJECT.search(old)
            and not NAMED_PERSON.search(old)
        ):
            return "Событие с неуказанным исполнителем стало действием говорящего или адресата."
        if ABSENCE.search(old) and TRANSMISSION.search(new) and not TRANSMISSION.search(before):
            return "Появилось получение или перемещение: исходник сообщал только о состоянии."
        # A prohibition on starting does not prohibit continuing an operation already in progress.
        left = re.split(r"[.!?;\n]", before[: span["start"]])[-1]
        right = re.split(r"[.!?;\n]", before[span["end"] :])[0]
        if (
            START.search(old)
            and STOP.search(left + old + right)
            and not START.search(left + new + right)
        ):
            return "В запрете потерялась привязка к началу действия."
    return ""


def review(body):
    evidence = body.get("minimal_check")
    if not isinstance(evidence, dict):
        raise ValueError("missing_minimal_review")
    axes = [evidence.get(key) for key in ("agent", "origin", "phase")]
    if (
        any(axis not in ("same", "changed", "unclear") for axis in axes)
        or evidence.get("outcome") not in ("both", "only_original", "only_proposal", "unclear")
        or evidence.get("syntax") not in ("natural", "defect", "unclear")
        or not all(
            isinstance(evidence.get(k), str) and evidence[k].strip() for k in ("scenario", "reason")
        )
    ):
        raise ValueError("invalid_minimal_review")
    if "changed" in axes or evidence["outcome"] in ("only_original", "only_proposal"):
        return evidence, "reject"
    if "unclear" in axes or evidence["outcome"] == "unclear":
        return evidence, "uncertain"
    return evidence, "pass"
