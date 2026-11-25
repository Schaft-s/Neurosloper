"""Equality witness for a single closed/open numeric bound. No model or ledger access."""

import re
from decimal import Decimal, localcontext


# Russian cardinals in the forms commonly used after a comparator. Compose hundreds,
# tens and units rather than equating individual adjacent number words.
CARDINALS = {
    0: "ноль нуль ноля нуля",
    1: "один одна одно одного одной одному одним",
    2: "два две двух двум двумя",
    3: "три трех трёх трем трём тремя",
    4: "четыре четырех четырёх четырем четырём четырьмя",
    5: "пять пяти пятью",
    6: "шесть шести шестью",
    7: "семь семи семью",
    8: "восемь восьми восемью",
    9: "девять девяти девятью",
    10: "десять десяти десятью",
    11: "одиннадцать одиннадцати",
    12: "двенадцать двенадцати",
    13: "тринадцать тринадцати",
    14: "четырнадцать четырнадцати",
    15: "пятнадцать пятнадцати",
    16: "шестнадцать шестнадцати",
    17: "семнадцать семнадцати",
    18: "восемнадцать восемнадцати",
    19: "девятнадцать девятнадцати",
    20: "двадцать двадцати",
    30: "тридцать тридцати",
    40: "сорок сорока",
    50: "пятьдесят пятидесяти",
    60: "шестьдесят шестидесяти",
    70: "семьдесят семидесяти",
    80: "восемьдесят восьмидесяти",
    90: "девяносто девяноста",
    100: "сто ста",
    200: "двести двухсот",
    300: "триста трехсот трёхсот",
    400: "четыреста четырехсот четырёхсот",
    500: "пятьсот пятисот",
    600: "шестьсот шестисот",
    700: "семьсот семисот",
    800: "восемьсот восьмисот",
    900: "девятьсот девятисот",
}
WORDS_TO_VALUE = {word: value for value, forms in CARDINALS.items() for word in forms.split()}
NUMBER_WORD = "(?:" + "|".join(sorted(WORDS_TO_VALUE, key=len, reverse=True)) + ")"
DIGITS = r"(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d+)?"
NUMBER = re.compile(
    r"(?<![\w.,:])(?P<num>(?:(?:минус|плюс)\s+|[+−-]?)(?:"
    + DIGITS
    + r"|(?:"
    + NUMBER_WORD
    + r"\s+){0,2}"
    + NUMBER_WORD
    + r"(?!\w)))(?!\d|[.,:]\d|[–—-]\d)",
    re.I,
)


def numeric_value(raw):
    raw = raw.casefold().replace("−", "-")
    sign = -1 if raw.startswith(("-", "минус")) else 1
    raw = re.sub(r"^(?:минус\s+|плюс\s+|[-+])", "", raw)
    if raw[0].isdigit():
        return Decimal(("-" if sign < 0 else "") + "".join(raw.split()).replace(",", "."))
    values = [WORDS_TO_VALUE[w] for w in raw.split()]
    for left, right in zip(values, values[1:]):
        if left < 20 or left >= 100 and right >= 100 or left < 100 and not 0 < right < 10:
            return None
    return Decimal(sign * sum(values))


UNITS = {
    "km_per_hour": r"(?:км\s*/\s*ч|километр\w* в час)",
    "m_per_second": r"(?:м\s*/\s*с|метр\w* в секунду)",
    "mg_per_l": r"(?:мг\s*/\s*л|миллиграмм\w* на литр)",
    "mmHg": r"мм\s+рт(?:утного)?\.?\s+ст(?:олба)?\.?",
    "celsius": r"(?:°\s*[CС]|градус\w*\s+(?:по\s+)?цельси\w*)",
    "fahrenheit": r"(?:°\s*[FФ]|градус\w*\s+(?:по\s+)?фаренгейт\w*)",
    "degree": r"(?:°|градус\w*)",
    "percent": r"(?:%|процент\w*)",
    "kg": r"(?:кг|килограмм\w*)",
    "g": r"(?:г|грамм\w*)",
    "mm": r"(?:мм|миллиметр\w*)",
    "cm": r"(?:см|сантиметр\w*)",
    "m": r"(?:м|метр\w*)",
    "km": r"(?:км|километр\w*)",
    "s": r"(?:с|сек(?:унд\w*)?)",
    "min": r"(?:мин(?:ут\w*)?)",
    "h": r"(?:ч|час\w*)",
    "pa": r"(?:па|паскал\w*)",
    "kpa": r"(?:кпа|килопаскал\w*)",
    "mpa": r"(?:мпа|мегапаскал\w*)",
    "v": r"(?:в|вольт\w*)",
    "a": r"(?:а|ампер\w*)",
    "db": r"(?:дб|децибел\w*)",
    "l": r"(?:л|литр\w*)",
    "ml": r"(?:мл|миллилитр\w*)",
}
UNIT = re.compile(r"\s*(?:" + "|".join(f"(?P<{k}>{v})" for k, v in UNITS.items()) + r")(?!\w)", re.I)
BOUND = re.compile(
    r"(?:\b(?P<le>(?:меньше|менее|ниже)\s+(?:или|либо)\s+(?:рав\w*|равно)\b|не\s+(?:свыше|более|больше|выше|превыш\w*|превыс\w*)|(?:как\s+)?максимум)|"
    r"\b(?P<ge>(?:больше|более|выше)\s+(?:или|либо)\s+(?:рав\w*|равно)\b|не\s+(?:менее|меньше|ниже)|(?:как\s+)?минимум)|"
    r"\b(?P<lt>менее|меньше|ниже)|\b(?P<gt>более|больше|выше|свыше|превыш\w*|превыс\w*)|"
    r"\b(?P<to>до)|\b(?P<from_>от)|(?P<symbol><=|>=|[≤≥<>]))"
    r"\s*(?:(?:чем|порога|отметки|значения|уровня|предела)\s+)?$",
    re.I,
)
NEGATED_OBSERVATION = re.compile(
    r"\bне\s+(?:показа\w*|показыва\w*|зарегистрир\w*|фиксир\w*|зафиксир\w*|наблюда\w*|"
    r"отме(?:ча|ти)\w*|измери\w*|бы(?:ло|ли|ла|л)|поднима\w*|подня\w*|опуска\w*|опусти\w*|"
    r"пада\w*|упа[лс]\w*|рос(?:ла|ло|ли)?|вырос\w*)\b"
    r"(?P<gap>(?:[ \t]+[а-яё-]+){0,6})[ \t]*$",
    re.I,
)
CONDITIONAL = re.compile(
    r"\b(?:кроме|исключ\w*|без|иначе|запрещ\w*|"
    r"разреш\w*|допус\w*|нельзя|можно|только|долж\w*|отказ\w*|прекра\w*|не|ни)\b",
    re.I,
)
PART = re.compile(r"[!?;\n]+|\.(?=\s*(?:$|[А-ЯЁA-Z«“]))")
WORD = re.compile(r"[а-яёa-z]+", re.I)
FILL = re.compile(
    r"^(?:за|на|в|по|к|у|из|от|до|с|со|и|а|это\w*|эт[а-яё]*|все\w*|вс[её]|"
    r"как|если|при|пока|когда|строго|врем\w*|период\w*|промежут\w*|интервал\w*|протяжени\w*|течени\w*|прибор\w*|датчик\w*|измерени\w*|показани\w*|"
    r"наблюдени\w*|результат\w*|значени\w*|отметк\w*|состав\w*|"
    r"остава\w*|держал\w*|бы(?:л|ли|ло|ла)|оказал\w*|находил\w*|"
    r"показ\w*|зафиксир\w*|зарегистрир\w*|не|ни|разу|разу|во|всегд\w*)$",
    re.I,
)
ENDINGS = re.compile(
    r"(?:иями|ями|ами|ыми|ими|ого|его|ому|ему|ией|ии|ью|ой|ей|ом|ем|ах|ях|ам|ям|ов|ев|ый|ий|ая|яя|ое|ее|ые|ие|ую|юю|а|я|ы|и|у|ю|е|о|ь)$",
    re.I,
)
SCALES = {
    "g": ("mass", 1),
    "kg": ("mass", 1000),
    "mm": ("length", 1),
    "cm": ("length", 10),
    "m": ("length", 1000),
    "km": ("length", 1000000),
    "s": ("duration", 1),
    "min": ("duration", 60),
    "h": ("duration", 3600),
    "ml": ("volume", 1),
    "l": ("volume", 1000),
    "pa": ("pressure", 1),
    "kpa": ("pressure", 1000),
    "mpa": ("pressure", 1000000),
}
OPS = {"le": ("upper", True), "lt": ("upper", False), "ge": ("lower", True), "gt": ("lower", False)}
SYMBOLS = {"≤": "le", "<=": "le", "<": "lt", "≥": "ge", ">=": "ge", ">": "gt"}
INVERT = {"gt": "le", "lt": "ge"}


# A time window regularly moves into or out of the sentence containing the bound.
# Remove only its numeric span, keeping equipment IDs, chamber numbers and other object labels.
TIME_WINDOW = re.compile(
    r"\b(?:с|от)\s+\d{1,2}(?::\d{2})?\s+(?:до|по)\s+\d{1,2}(?::\d{2})?(?:\s+(?:час\w*|минут\w*))?\b|"
    r"\b(?:за|в течение)\s+\d+\s+(?:час\w*|минут\w*|сут\w*|смен\w*)\b",
    re.I,
)
MEASURANDS = {
    "temperature": r"температур|термометр",
    "pressure": r"давлени|манометр|барометр",
    "mass": r"масс[а-яё]*|взвеш|весы",
    "speed": r"скорост|спидометр",
    "sound": r"громкост|шумомер",
    "voltage": r"напряжени|вольтметр",
    "humidity": r"влажност|гигрометр",
}


POST_BOUND = re.compile(
    r"\s+(?:или|либо|и)\s+(?:(?P<ge>больше|более|выше)|(?P<le>меньше|менее|ниже))\b", re.I
)
OPERATING_STATE = re.compile(
    r"\b(?:пока|когда|если)\s+(?:станок|оборудование|устройство|агрегат|аппарат|машина|механизм)\s+"
    r"(?:работа\w*|находи\w*|действова\w*)\s+(?=на\b|при\b|в\b)",
    re.I,
)


def context_words(text):
    text = OPERATING_STATE.sub(" ", TIME_WINDOW.sub(" ", text))
    words = set(re.findall(r"(?:№\s*)?\d+(?:[.,]\d+)?", text))
    for raw in WORD.findall(text):
        if FILL.fullmatch(raw):
            continue
        word = raw.casefold()
        if re.search(r"[аеия]ни(?:е|я|ю|и|ем|ями|ям|ях|й)$", word):
            word = re.sub(r"(ни)(?:е|я|ю|и|ем|ями|ям|ях|й)$", r"\1", word)
        elif len(word) >= 4:
            word = ENDINGS.sub("", word)
        for name, pattern in MEASURANDS.items():
            if re.match(pattern, raw, re.I):
                word = "@" + name
                break
        words.add(word)
    return words


def quantity(text, num):
    value = numeric_value(num["num"])
    if value is None:
        return None
    unit = UNIT.match(text, num.end())
    name = unit.lastgroup if unit else ""
    end = unit.end() if unit else num.end()
    # Never silently drop an unfamiliar unit or a compound unit's denominator.
    tail = text[end:]
    if re.match(r"\s*(?:/|²|³|\^|на\s+(?:час|секунд|минут|метр|килограмм))", tail, re.I):
        return None
    if not unit:
        token = re.match(r"\s*([a-zа-яё]+|[°%])", tail, re.I)
        if token and not re.fullmatch(
            r"включительно|и|или|либо|но|за|во|на|при|когда|если|будет|была|был|было", token[1], re.I
        ):
            # A count can also be bound, using its noun rather than guessing its unit.
            name = "word:" + next(iter(context_words(token[1])), token[1].casefold())
            end += token.end()
    dimension, scale = SCALES.get(name, (name, 1))
    with localcontext() as precision:
        precision.prec = max(28, len(value.as_tuple().digits) + 10)
        return str(value * scale), dimension, end


def extract(text):
    out = []
    for sentence in PART.split(text):
        for num in NUMBER.finditer(sentence):
            left = sentence[: num.start()]
            m = BOUND.search(left)
            q = quantity(sentence, num)
            if not q:
                continue
            value, unit, end = q
            post = POST_BOUND.match(sentence[end:])
            if not m and not post:
                continue
            if post:
                if m and m.lastgroup not in ("from_", "to"):
                    continue
                bound = post.lastgroup
                end += post.end()
            else:
                bound = m.lastgroup
                if bound == "symbol":
                    bound = SYMBOLS[m["symbol"]]
            after = sentence[end:]
            included = re.match(r"\s+включительно\b", after, re.I)
            if bound in ("to", "from_"):
                if not included:
                    continue
                bound = "le" if bound == "to" else "ge"
            if included:
                end += included.end()
            prefix = left[: m.start()] if m else left
            # "не показал значений выше x" rules out >x, so x is permitted.
            observation = NEGATED_OBSERVATION.search(prefix)
            gap = ""
            if observation:
                if bound not in INVERT or re.search(
                    r"\b(?:не|ни|или|и|если|что|чтобы|но)\b", observation["gap"], re.I
                ):
                    continue
                bound = INVERT[bound]
                gap = observation["gap"]
                prefix = prefix[: observation.start()]
            if included and not OPS[bound][1]:
                continue
            prefix = re.sub(r"\b(?:ни разу|никогда)\s*$", "", prefix, flags=re.I)
            orientation, closed = OPS[bound]
            context = (prefix + " " + gap + " " + sentence[end:]).strip()
            out.append(
                dict(
                    value=value,
                    unit=unit,
                    orientation=orientation,
                    inclusive=closed,
                    quote=sentence.strip(),
                    context=context,
                )
            )
    return out


def compare(before, after):
    """Compare grounded scalar bounds. 'no_veto' expresses no new decision about equivalence."""
    left, right = extract(before), extract(after)
    result = dict(status="no_veto", reason="", source=left, proposal=right)
    if len(left) != 1 or len(right) != 1:
        return result
    a, b = left[0], right[0]
    if (
        a["orientation"] != b["orientation"]
        or a["inclusive"] == b["inclusive"]
        or Decimal(a["value"]) != Decimal(b["value"])
    ):
        return result
    if a["unit"] != b["unit"] and a["unit"] and b["unit"]:
        return result

    def repeated(text, frame):
        count = 0
        for sentence in PART.split(text):
            for match in NUMBER.finditer(sentence):
                q = quantity(sentence, match)
                if q and Decimal(q[0]) == Decimal(frame["value"]) and q[1] == frame["unit"]:
                    count += 1
            if count > 1:
                return True
        return False

    if repeated(before, a) or repeated(after, b):
        return result
    # Numeric conditions can encode the complement under an inverted action or permission.
    same_context = " ".join(a["context"].lower().split()) == " ".join(b["context"].lower().split())
    if not same_context and (CONDITIONAL.search(a["context"]) or CONDITIONAL.search(b["context"])):
        return result
    aw, bw = context_words(a["context"]), context_words(b["context"])
    if not same_context and not aw & bw:
        readings = re.compile(r"\b(?:значени|показани)\w*", re.I)
        if (
            aw
            or bw
            or not a["unit"]
            or not (readings.search(a["context"]) and readings.search(b["context"]))
        ):
            return result
    if not same_context and aw - bw and bw - aw:
        # Shared quantity names alone do not identify two different subjects.
        return result
    if not same_context and aw != bw:
        # A sole apparatus/property plus a single unit and value can refer back to an
        # object named in a neighboring sentence. Keep competing local objects apart.
        shared_measure = any(w.startswith("@") for w in aw & bw) or bool(a["unit"] and aw & bw)
        aligned = shared_measure and aw - bw <= context_words(after) and bw - aw <= context_words(before)
    else:
        aligned = True
    if a["unit"] != b["unit"] or not aligned:
        result.update(
            status="uncertain",
            reason="При равенстве порогу вывод меняется, но единица или объект наблюдения сопоставлены неоднозначно.",
        )
        return result
    result.update(
        status="reject",
        reason="При точном равенстве порогу одна формулировка допускает значение, другая исключает его.",
    )
    return result


def replay(before, after, previous_result):
    """Reapply only the local equality witness to a saved result, preserving all recorded family votes."""
    checked = compare(before, after)
    result = dict(previous_result, boundary_review=checked)
    if result.get("policy") == "relations_v3":
        result.update(previous_policy="relations_v3", policy="relations_v4")
    elif result.get("policy") != "relations_v4":
        result["replay_policy"] = "relations_v4_boundary_only"
    if checked["status"] in ("reject", "uncertain") and result.get("status") != "reject":
        result.update(status=checked["status"], reason=checked["reason"])
    return result
