"""Deterministic gates shared by MCP, review and export."""

import re
from bisect import bisect_right
from collections import Counter
from decimal import Decimal

import boundary_review

STYLES = {
    "voice": "Сохранить голос автора",
    "direct": "Жёстко и прямо, без новых требований",
    "formal": "Юридически формально, сохранить прежнюю модальность",
    "friendly": "По-дружески и мягко, сохранить обязательность",
}
STYLE_ALIASES = {"neutral": "direct", "casual": "friendly"}
LENGTHS = {
    "free": "Свободная длина",
    "short": "Не длиннее исходного блока",
    "near": "Близко к исходнику (±10%)",
    "fit": "В рамку PPTX; для других файлов — не длиннее",
}
PRECISIONS = {
    "minimal": "Минимальная правка выбранной цитаты",
    "free": "Свободное выражение в явно выбранном блоке",
    "literal": "Минимальная правка с дословной защитой",
}
NUM = re.compile(
    r"\d+(?:[.,:/–-]\d+)*(?:[ \u00a0]?(?:%|₽|€|\$|кг|г|км|см|мм|мс|м|мл|л|МБ|ГБ|млн|млрд|руб(?:лей|ль|ля)?|час(?:а|ов)?|минут(?:а|ы|у)?|секунд(?:а|ы|у)?|сут(?:ки|ок)|дн(?:я|ей)|день|недел(?:я|и|ь)|месяц(?:а|ев)?|год(?:а|ов)?|лет)(?!\w))?",
    re.I,
)
ANCHOR = re.compile(
    r"https?://[^\s)]+|[\w.+-]+@[\w.-]+|\b(?:не менее|не более|не ранее|не позднее|только если|если|кроме|исключая|не|ни|без|вправе|запрещ(?:ено|ается)|разреш(?:ено|ается)|мож(?:ет|но)|допускается|просим|нельзя)\b",
    re.I,
)
QUOTED = re.compile(r'«[^»\n]+»|“[^”\n]+”|"[^"\n]+"')
WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


# Obligation markers belong to the fidelity gate only. Equivalent expressions share one
# class, while “не должен” and “не обязан” have different normative consequences.
OBLIGATION = re.compile(
    r"\b(?:(?P<neg>не)\s+)?(?P<modal>долж(?:ен|на|но|ны)|обязан(?:а|о|ы)?|"
    r"обязательн(?:о|а|ы|ый|ая|ое|ые)|обязателен|в\s+обязательном\s+порядке|"
    r"необходим(?:о|а|ы)?|необходимые?|нуж(?:ен|но|на|ны)|требу(?:ется|ются)|"
    r"подлеж(?:ит|ат)|надлежит|предписано|вменяется\s+в\s+обязанность)\b",
    re.I,
)
WEAKENING = re.compile(
    r"\b(?:следует|стоит|рекоменду(?:ется|ют)|рекомендовано|желательно|"
    r"обычно|зачастую|как\s+правило|по\s+возможности|по\s+желанию)\b",
    re.I,
)
CLAUSE = re.compile(r"[.!?;:\n]+|,\s*(?=(?:а|но|тогда\s+как)\b)", re.I)
GRAMMAR = {
    "этот",
    "этого",
    "этом",
    "этой",
    "также",
    "того",
    "тому",
    "если",
    "быть",
    "были",
    "будет",
    "будут",
    "только",
    "после",
    "перед",
    "когда",
    "чтобы",
    "лишь",
    "этим",
    "этих",
    "своей",
    "которые",
}


def obligation_kind(match):
    if not match["neg"]:
        return "required"
    return "prohibited" if match["modal"].casefold().startswith("долж") else "exempt"


def obligation_context(text):
    kinds, weakened, words = Counter(), Counter(), {}
    for clause in CLAUSE.split(text):
        matches = list(OBLIGATION.finditer(clause))
        scope = frozenset(obligation_kind(m) for m in matches)
        kinds.update(obligation_kind(m) for m in matches)
        if matches:
            weakened.update(WEAKENING.findall(clause))
        for word in WORD.findall(OBLIGATION.sub(" ", clause)):
            if len(word) >= 4 and word not in GRAMMAR:
                words.setdefault(word, set()).add(scope)
    return kinds, weakened, words


def obligation_error(old, new):
    if not OBLIGATION.search(old) and not OBLIGATION.search(new):
        return ""
    before, weak_before, lexical_before = obligation_context(old)
    after, weak_after, lexical_after = obligation_context(new)
    if before != after:
        # One modal may govern a list of duties, each restated explicitly in separate sentences.
        # Allow that resegmentation only if the substantive vocabulary is preserved exactly.
        def content(text):
            return Counter(
                w for w in WORD.findall(OBLIGATION.sub(" ", text)) if w not in {"и", "а", "также"}
            )

        if set(before) != set(after) or content(old) != content(new):
            return "Не подтверждена прежняя сила требования: изменилась обязанность, запрет или освобождение от неё."
    if weak_after != weak_before:
        return "Изменилась сила требования: рядом с обязанностью изменено указание на рекомендацию или обычную практику."
    for word in lexical_before.keys() & lexical_after.keys():
        left, right = lexical_before[word], lexical_after[word]
        if len(left) == len(right) == 1 and left != right:
            return f"Не подтверждена привязка требования к исходному действию или участнику («{word}»)."
    return ""


def obligation_evidence(text, claims, side):
    """Every explicit modality must be inside an aligned quotation on both sides."""
    other = "after" if side == "before" else "before"
    obligations = list(OBLIGATION.finditer(text))
    if not obligations:
        return True
    covered = set()
    for claim in claims:
        quote = claim[side]
        opposite = claim[other]
        if not quote or not opposite:
            continue
        kinds = {obligation_kind(m) for m in OBLIGATION.finditer(opposite)}
        if not kinds:
            continue
        anchors = [m for m in OBLIGATION.finditer(quote) if obligation_kind(m) in kinds]
        # A bare modal is insufficient evidence about what was required.
        if not anchors or not any(
            len(w) >= 3 and w.casefold() not in GRAMMAR for w in WORD.findall(OBLIGATION.sub(" ", quote))
        ):
            continue
        start = text.find(quote)
        if start < 0 or text.find(quote, start + 1) >= 0:
            continue
        covered.update(start + m.start() for m in anchors)
    return all(m.start() in covered for m in obligations)


def literal_terms(terms):
    if isinstance(terms, str):
        return [t.strip() for t in re.split(r"[,\n]", terms) if t.strip()]
    if not isinstance(terms, (list, tuple)) or not all(isinstance(t, str) for t in terms):
        raise ValueError("Защищённые выражения должны быть строками")
    return list(dict.fromkeys(t for t in terms if t))


def numeric_links(text):
    # Relative placement of unique shared words catches exchanging two entity labels
    # while the numbers themselves remain in the same order. Deliberately conservative.
    ends = [match.end() for match in NUM.finditer(text)]
    if len(ends) < 2:
        return {}
    words = [w for w in WORD.finditer(text) if len(w[0]) >= 2]
    counts = Counter(w[0] for w in words)
    return {w[0]: bisect_right(ends, w.start()) for w in words if counts[w[0]] == 1}


def allowed_ranges(old, new, spans):
    """Match untouched pieces in order, including exact prefix/suffix (linear search)."""
    if not spans:
        return False
    cursor = 0
    fixed = []
    for span in sorted(spans, key=lambda s: s["start"]):
        start, end = span["start"], span["end"]
        if type(start) is not int or type(end) is not int or not cursor <= start < end <= len(old):
            return False
        if "quote" in span and old[start:end] != span["quote"]:
            return False
        fixed.append(old[cursor:start])
        cursor = end
    fixed.append(old[cursor:])
    prefix, suffix = fixed[0], fixed[-1]
    if not new.startswith(prefix) or not new.endswith(suffix) or len(prefix) + len(suffix) > len(new):
        return False
    cursor = len(prefix)
    stop = len(new) - len(suffix)
    for piece in fixed[1:-1]:
        start = new.find(piece, cursor, stop)
        if start < 0:
            return False
        cursor = start + len(piece)
    return True


# Each pair expresses the same logical relation. Different modalities (permission,
# possibility, request, advice), negated duties, magnitudes and units never share a key.
ANCHOR_PARAPHRASES = (
    (r"не\s+(?:позднее|позже)", "не позднее"),
    (r"не\s+(?:ранее|раньше)", "не ранее"),
    (r"(?:кроме|исключая|за\s+исключением)", "кроме"),
    (r"(?:только|лишь)\s+если", "только если"),
    (r"(?:запрещено|запрещается)", "запрещено"),
    (r"(?:разрешено|разрешается)", "разрешено"),
    (r"(?:вправе|име(?:ет|ют)\s+право)", "вправе"),
    (r"(?:просим|обращаемся\s+с\s+просьбой)", "просим"),
)


def canonical_phrasing(text):
    for pattern, replacement in ANCHOR_PARAPHRASES:
        text = re.sub(r"\b(?:" + pattern + r")\b", replacement, text)
    return text


def phrasing_evidence(before, after, claims):
    for pattern, _ in ANCHOR_PARAPHRASES:
        relation = re.compile(r"\b(?:" + pattern + r")\b", re.I)
        old, new = before.casefold(), after.casefold()
        if Counter(relation.findall(old)) == Counter(relation.findall(new)):
            continue

        def cites(text):
            return relation.search(text) and any(
                len(w) >= 3 for w in re.findall(r"\w+", relation.sub(" ", text))
            )

        if not any(cites(c["before"]) and cites(c["after"]) for c in claims):
            return False
    return True


# Literal references carry identity independently of the wording of a requirement.
REFERENCE = re.compile(
    r"https?://[^\s<>()«»]+|[\w.+-]+@[\w.-]+\.\w+|`[^`\n]+`|"
    r"\b(?=[A-ZА-ЯЁ0-9_-]*\d)"
    r"[A-ZА-ЯЁ][A-ZА-ЯЁ0-9]*(?:[-_][A-ZА-ЯЁ0-9]+)+\b"
)


def literal_references(text):
    return [m[0].rstrip(".,;:!?") for m in REFERENCE.finditer(text)] + QUOTED.findall(text)


def verbatim_error(old, new, terms):
    if Counter(QUOTED.findall(old)) != Counter(QUOTED.findall(new)):
        return "Изменена дословная цитата"
    protected = literal_terms(terms)
    if old != new and old in protected:
        return "Блок целиком защищён дословно."
    if any(old.count(t) != new.count(t) for t in protected):
        return "Изменена защищённая строка"
    if len(protected) > 1:

        def sequence(value):
            positions = [
                (m.start(), term) for term in protected for m in re.finditer(re.escape(term), value)
            ]
            return [term for _, term in sorted(positions)]

        if sequence(old) != sequence(new):
            return "Изменён порядок защищённых строк"
    return ""


# A different measured quantity in exactly the same syntactic slot is decidable.
# Restructured sentences, spelled-out quantities and unfamiliar units go to the judges.
UNIT_GROUPS = {
    ("time", 1): r"секунд(?:а|ы|у|е|ой)?|сек|с",
    ("time", 60): r"минут(?:а|ы|у|е|ой)?|мин",
    ("time", 3600): r"час(?:а|ов|у|е|ом)?|ч",
    ("length", 1): r"мм|миллиметр(?:а|ов)?",
    ("length", 10): r"см|сантиметр(?:а|ов)?",
    ("length", 1000): r"м|метр(?:а|ов)?",
    ("length", 1000000): r"км|километр(?:а|ов)?",
    ("mass", 1): r"г|грамм(?:а|ов)?",
    ("mass", 1000): r"кг|килограмм(?:а|ов)?",
    ("fraction", 1): r"%|процент(?:а|ов)?",
}
UNITS = list(UNIT_GROUPS)
MEASURE = re.compile(
    r"(?<![\w.,])(?P<number>[−+-]?\d+(?:[.,]\d+)?)\s*"
    + "(?:"
    + "|".join(f"(?P<u{i}>{pattern})" for i, pattern in enumerate(UNIT_GROUPS.values()))
    + ")"
    + r"(?!\w)",
    re.I,
)
EXPLICIT_FORCE = re.compile(
    r"\b(?:не\s+обязан(?:а|о|ы)?|не\s+долж(?:ен|на|но|ны)|"
    r"обязан(?:а|о|ы)?|долж(?:ен|на|но|ны)|вправе|име(?:ет|ют) право|"
    r"разреш(?:ено|ается)|запрещ(?:ено|ается)|следует|рекомендуется)\b",
    re.I,
)


BOUND_TYPES = {
    "at_most": r"не (?:больше|более|выше)|максимум",
    "at_least": r"не (?:меньше|менее|ниже)|минимум",
    "less": r"меньше|менее",
    "greater": r"больше|более",
    "equal": r"ровно",
}
BOUND = re.compile(
    r"\b(?:" + "|".join(f"(?P<{name}>{words})" for name, words in BOUND_TYPES.items())
    + r")(?=\s+\d)", re.I
)


def slot_values(pattern, text, value):
    values = []

    def slot(match):
        values.append(value(match))
        return "〈слот〉"

    return pattern.sub(slot, text.casefold()), values


def measured_value(match):
    dim, scale = UNITS[int(match.lastgroup[1:])]
    return dim, Decimal(match["number"].replace(",", ".").replace("−", "-")) * scale


def explicit_force(match):
    word = match[0]
    if word.startswith(("не долж", "запрещ")):
        return "prohibited"
    if word.startswith("не обязан"):
        return "exempt"
    if word.startswith(("долж", "обязан")):
        return "required"
    if word.startswith(("следует", "рекомендуется")):
        return "advice"
    return "permitted"


def relation_preflight(old, new, terms):
    error = verbatim_error(old, new, terms)
    if error:
        return error
    if Counter(literal_references(old)) != Counter(literal_references(new)):
        return "Изменена дословная ссылка, идентификатор или адрес."
    for pattern, value, reason in (
        (MEASURE, measured_value, "Изменена величина или её размерность."),
        (BOUND, lambda match: match.lastgroup, "Изменена граница допустимого диапазона."),
        (
            EXPLICIT_FORCE,
            explicit_force,
            "При том же действии изменилась сила требования или запрета.",
        ),
    ):
        left, left_slots = slot_values(pattern, old, value)
        right, right_slots = slot_values(pattern, new, value)
        if left == right and left_slots != right_slots:
            return reason
    return ""


def check(block, new, length, terms, *, reviewed_phrasing=False, relation_review=False, equality_review=False):
    old = block["text"]
    if length not in LENGTHS:
        return "Неизвестный режим длины"
    if not block["edit"] or not isinstance(new, str) or new == old:
        return "Недоступная или пустая операция"
    if len(new) > min(100000, len(old) * 4 + 80) or any(ord(c) < 32 and c not in "\n\t" for c in new):
        return "Некорректный размер или символ"
    if "\n" in new and "\n" not in old:
        return "Изменена структура абзаца"
    if (length == "short" or length == "fit" and "shape" not in block) and len(new) > len(old):
        return "Превышен предел длины"
    if length == "near" and abs(len(new) - len(old)) > max(1, len(old) * 0.1):
        return "Изменение превышает 10%"
    if equality_review:
        boundary = boundary_review.compare(old, new)
        if boundary["status"] != "no_veto":
            return boundary["reason"]
    if relation_review:
        return relation_preflight(old, new, terms)
    a, b = old.casefold(), new.casefold()
    if NUM.findall(a) != NUM.findall(b):
        return "Изменены числа, единицы или порядок чисел"
    links_a, links_b = numeric_links(a), numeric_links(b)
    if any(links_a[w] != links_b[w] for w in links_a.keys() & links_b.keys()):
        return "Изменилась привязка числа к участнику или единице"
    requirement_error = obligation_error(a, b)
    if requirement_error:
        return requirement_error
    old_anchors, new_anchors = OBLIGATION.sub(" ", a), OBLIGATION.sub(" ", b)
    if reviewed_phrasing:
        # Called for preflight and for the exact proposal approved by independent families.
        # Keep all other gates on the original text. Check exact relation counts also when
        # the strict tokenizer did not recognize the original side of a paraphrase.
        strict_counts_differ = Counter(ANCHOR.findall(old_anchors)) != Counter(
            ANCHOR.findall(new_anchors)
        )
        old_anchors, new_anchors = (
            canonical_phrasing(old_anchors),
            canonical_phrasing(new_anchors),
        )
        if strict_counts_differ and re.findall(r"\w+|[^\w\s]", canonical_phrasing(a)) != re.findall(
            r"\w+|[^\w\s]", canonical_phrasing(b)
        ):
            return "Вместе с формулировкой ограничения изменены другие слова или их привязка."
    if Counter(ANCHOR.findall(old_anchors)) != Counter(ANCHOR.findall(new_anchors)):
        return "Изменены адрес, отрицание, модальность или условие"
    return verbatim_error(old, new, terms)
