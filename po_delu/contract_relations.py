"""Authority and record identity are relations, not inventories of shared words."""

import re


AUDIT = """Составь contract_checks независимо для original и proposal. Ищи два типа отношений, даже если во второй версии исчезло само название процедуры.
authority: кто и что может предложить, запросить или инициировать, кто принимает решение, какой результат возникает без согласия другого участника? Инициатор может подать ходатайство, но сам этот акт не утверждает результат. Если разрешение или чужое согласие — условие действия, учти его. Проверь ситуацию «инициатор настаивает, полномочный участник отклоняет предложение»: произойдёт ли желаемое изменение по воле первого? Не считай возможность поставить вопрос правом на положительный ответ.
identity: какие именно единицы учёта объединяют? Чем определяется один и тот же объект/случай? Установи ключ и учитывай условия; автор, дата или канал сообщения могут быть лишь контекстом. Проверь две ситуации, оставляя все остальные признаки одинаковыми: (1) одновременно от одного человека через один канал пришли сообщения о двух разных объектах с разными идентификаторами; (2) тот же самый объект упомянут через два разных канала. Сколько учётных единиц образуется? Отдельно проверь добавление или изъятие составной части ключа.
Добавь "contract_checks":{"authority":[],"identity":[]} к обычному ответу. Включи строку для каждой пары сопоставляемых отношений, разделяй разных акторов и действия. В каждой строке поля "s" и "t" — ОТДЕЛЬНЫЕ наблюдения по цитате своей версии. Если отношение исчезло, на соответствующей стороне null. Для authority пример формы стороны: {"unit":"s1","quote":"дословная опора","actor":"кто","action":"какое изменение","power":"request|decide|conditional_decision|other","denied":"effective|not_effective|unspecified"}. denied означает, может ли по правилам ЭТОГО текста первый добиться изменения собственным решением при отклонённом запросе. Если текст даёт только право попросить, ставь not_effective. Если названо право совершить само изменение без внешнего согласия, ставь effective. Не приписывай согласование по привычке.
Для identity форма стороны: {"unit":"s1","quote":"дословная опора","entity":"что считается","keys":["object|person|time|channel|other"],"two_objects":"one|two|unspecified","two_channels":"one|two|unspecified"}. keys — массив ТОЛЬКО обязательных признаков совпадения, без примеров, каналов подачи и прочей обстановки. Разные номера идентифицируемых объектов дают two при ключе объекта, даже когда у объектов общий владелец. Разные объекты дают one при правиле «один человек за этот период». Если условие или база в тексте остаются неизвестными, ставь unspecified. Дай отдельные фактические ответы для каждого текста, затем сопоставь. Для отсутствующего в обеих версиях типа верни пустой список."""


REQUEST = re.compile(
    r"\b(?:предложи\w*|предлага\w*|попроси\w*|попросит\w*|попрос[яи]\w*|прос(?:ить|ит|ят|ьб)\w*|"
    r"запросить|запрашива\w*|ходатайств\w*|инициир\w*|под(?:ать|а[её]т|ают) (?:запрос|заявк|ходатайство)|"
    r"(?:внести|внос\w*|направ\w*) предложени\w*|вынести .{0,25}на (?:обсуждение|рассмотрение))",
    re.I,
)
POWER = re.compile(
    r"\b(?:вправе|мож(?:ет|но|но будет|ешь|ете|ем)|могут|име\w* право|разреш\w*|допуска\w*|право)\b",
    re.I,
)
ENACT = re.compile(
    r"\b(?:реш(?:ает|ают|ить|ает)|утвержда\w*|утверди\w*|принима\w* решени\w*|принять решение|"
    r"назнач\w*|перен(?:ести|осить|осит|осит[ьъ])|сдви[гн]\w*|меня(?:ть|ет|ют)|измен(?:ить|яет|ять|яют)|"
    r"устан(?:овить|овит|авлива)\w*|продле\w*|продли\w*|отмен(?:ить|ять|яет)|"
    r"выбира(?:ет|ют|ть)|выбрать|определ(?:ить|яет|яют|ять)|распоря(?:жается|диться))\b",
    re.I,
)
DIRECT = re.compile(
    r"\b(?:единолично|самостоятельно|сам|сама|сами|без (?:чьего-либо )?(?:согласования|одобрения|разрешения))\b",
    re.I,
)
CONDITION = re.compile(
    r"\b(?:если|после соглас\w*|при (?:наличии |условии )?(?:соглас\w*|одобр\w*|разреш\w*)|с (?:согласия|разрешения)|"
    r"согласовав|по согласованию|согласован\w*|только при|лишь при)\b",
    re.I,
)
NEGATIVE = re.compile(r"\b(?:не|нельзя|запрещ\w*)\b", re.I)
GROUP = re.compile(
    r"\b(?:дубл\w*|повтор\w*|объедин\w*|группир\w*|слива\w*|сгруппир\w*|"
    r"свод\w* в од\w*|одн(?:им|ой|у|ого|а) (?:запис\w*|случа\w*|обращен\w*|единиц\w*)|"
    r"(?:счита\w*|учитыва\w*|регистрир\w*) (?:как |за )?од\w*|"
    r"один раз|отдельн\w* запис\w*)\b",
    re.I,
)
PERSON = r"(?:человек|люд|лиц[ауом]|владел|владель|клиент|пользовател|посетител|участник|сотрудник|работник|заявител|отправител|покупател|автор|пациент|гост[ьяе]|курьер|абонент|жител|родител|читател|водител|пассажир|спортсмен|ученик|учени[цк]|экскурсант|граждан)\w*"
OBJECT = r"(?:идентификатор|номер|объект|дел[оауе]|случа[йяею]|заказ|заявк|обращени|происшестви|экземпляр|инцидент|договор|страховк|талон|билет|путёвк|путевк|карточк|документ|операци|плат[её]ж|рейс|проект|прибор|экспонат|мероприяти|доставк|эпизод|проблем|поломк|неисправност|событи|брон[ьи]|предмет)\w*"
TIME = r"(?:дат[ауыое]|сут[ко]|день|дня|дн[её]м|недел|смен[ауыое]|период|час[а-яё]*|месяц|утро)\w*"
CHANNEL = r"(?:канал|источник|способ(?:а|ом)? связи)\w*"
KEY_PREFIX = (
    r"(?:од(?:ин|н\w{1,4})|обоих|оба|обе|том же|той же|того же|тому же|тем же|та же|тот же|"
    r"одинаков\w*|совпадающ\w*|по|за|в пределах|на протяжении|в течение|кажд\w*)\s+"
    r"(?:(?:и\s+)?(?:тот|та|то|том|той|ту|тому|того|тем|туда)\s+же\s+)?"
)
KEY_PATTERNS = {
    name: re.compile(r"\b" + KEY_PREFIX + root + r"\b", re.I)
    for name, root in (("object", OBJECT), ("person", PERSON), ("time", TIME), ("channel", CHANNEL))
}
IGNORED = re.compile(
    r"(?:независимо от|без уч[её]та|вне зависимости от|не по|даже если|хотя)\s*[^,.;!?]{0,50}$", re.I
)
SENTENCES = re.compile(r"[.!?;\n]+")
NEGATED_POWER = re.compile(r"\bне\s+(?:вправе|мож\w*|мог\w*|име\w*|разреш\w*)", re.I)


def authority(text):
    entries = []
    relevant = False
    for raw in SENTENCES.split(text):
        clause = raw.strip()
        request, power, enact = REQUEST.search(clause), POWER.search(clause), ENACT.search(clause)
        independent = DIRECT.search(clause)
        relevant |= bool(
            (request and (power or enact or DIRECT.search(text))) or (enact and (power or independent))
        )
        if not (request or (enact and (power or independent))) or NEGATED_POWER.search(clause):
            continue
        event = request or enact
        # Do not classify an embedded action by the verb inside the request.
        if request and enact and enact.start() < request.start():
            continue
        if power and event.start() < power.start():
            continue
        if NEGATIVE.search(clause[: event.start()]) or CONDITION.search(clause):
            continue
        actor = clause[: power.start()].strip(" ,:—–-").casefold() if power else ""
        entries.append(
            dict(
                kind="request" if request else "decide",
                quote=clause,
                actor=actor,
                explicit=bool(power or independent),
            )
        )
    return relevant, entries


def identity(text):
    entries = []
    for raw in SENTENCES.split(text):
        sentence = raw.strip()
        marker = GROUP.search(sentence)
        if not marker or (
            marker[0].casefold().startswith("повтор")
            and not re.search(
                r"дубл|запис|уч[её]т|учитыв|счита|объедин|номер|идентификатор", sentence, re.I
            )
        ):
            continue
        keys = set()
        matches = []
        for axis, pattern in KEY_PATTERNS.items():
            for match in pattern.finditer(sentence):
                if not IGNORED.search(sentence[: match.start()]):
                    keys.add(axis)
                    matches.append(match[0])
        entries.append(
            dict(
                keys=sorted(keys),
                quote=sentence,
                matches=matches,
                negated=bool(NEGATIVE.search(sentence)),
            )
        )
    return bool(entries), entries


EFFORT = re.compile(
    r"\b(?:по)?пыт(?:аться|ка|ки|ку|ок)\b|\b(?:постараться|стараться|постарается|стремиться)\b|\b(?:принять|принимать|предпринять|предпринимать|прилагает|приложить|прилагать)\s+(?:все\s+|разумные\s+|необходимые\s+)?(?:меры|усилия)\b",
    re.I,
)
DUTY = re.compile(
    r"\b(?:обязан\w*|долж(?:ен|на|ны|но)|обязуется|гарантиру\w*|требуется|обязательно)\b", re.I
)
EFFORT_AUDIT = """Для этой авторской правки в contract_checks добавь также effort: массив сравнений обязанности предпринять меры с обязанностью гарантировать результат. Для каждой стороны укажи {"unit":"s1","quote":"дословная опора","undertaking":"attempt|result|undetermined","failed_after_attempt":"complies|breach|undetermined"}. Контрольная ситуация: все требуемые разумные действия выполнены, но результат не достигнут. Разбери обе версии раздельно, не додумывай гарантию в отсутствие опоры. При отсутствии этого вопроса в обеих версиях верни effort=[]."""


def compare(before, after, generated=False):
    audit = {}
    for axis, extract in (("authority", authority), ("identity", identity)):
        have_s, source = extract(before)
        have_t, target = extract(after)
        if not (have_s or have_t):
            continue
        item = dict(status="review", source=source, proposal=target)
        if before != after and len(source) == len(target) == 1:
            a, b = source[0], target[0]
            if axis == "authority" and a["kind"] != b["kind"] and a["explicit"] and b["explicit"]:
                # In both directions hard veto is reserved for the literal removal of
                # an explicit request operator from an otherwise identical single rule.
                requested = a if a["kind"] == "request" else b
                decided = b if a["kind"] == "request" else a
                shared_actor = (
                    a["actor"]
                    and a["actor"] == b["actor"]
                    and len([x for x in SENTENCES.split(before) if x.strip()]) == 1
                    and len([x for x in SENTENCES.split(after) if x.strip()]) == 1
                    and " ".join(
                        re.sub(
                            r"\b(?:предложить|попросить)\b", "", requested["quote"].casefold()
                        ).split()
                    )
                    == " ".join(decided["quote"].casefold().split())
                )
                item.update(
                    status="reject" if shared_actor else "uncertain",
                    reason="Инициирование и самостоятельное принятие решения дают разные права при отказе.",
                )
            if axis == "identity" and not (a["negated"] or b["negated"]):
                left, right = set(a["keys"]), set(b["keys"])
                if left and right and ("object" in left) != ("object" in right):
                    # Multiple key expressions can describe other circumstances. Only a clean change
                    # from an object key to an explicit person key (or vice versa) proves this witness.
                    person_side = right if "object" in left else left
                    object_side = left if "object" in left else right
                    if "person" in person_side and "person" not in object_side:
                        item.update(
                            status="uncertain",
                            reason="Изменился возможный критерий объединения разных объектов одного человека.",
                        )
                        if len(a["matches"]) == len(b["matches"]) == 1:
                            sa, sb = a["matches"][0], b["matches"][0]
                            # Identical explicit grouping predicates with only the named key replaced.
                            if (
                                sa.casefold().startswith("по ")
                                and sb.casefold().startswith("по ")
                                and re.search(r"\b(?:группир\w*|объедин\w*)\b", a["quote"], re.I)
                                and a["quote"].replace(sa, "〈ключ〉").casefold()
                                == b["quote"].replace(sb, "〈ключ〉").casefold()
                                and len([x for x in SENTENCES.split(before) if x.strip()]) == 1
                                and len([x for x in SENTENCES.split(after) if x.strip()]) == 1
                            ):
                                item["status"] = "reject"
        if axis == "identity" and item["status"] == "review":
            # The witness remains undecidable locally if repeated clauses use multiple labels.
            def key_sets(entries):
                return {k for x in entries if not x["negated"] for k in x["keys"]}

            s_keys, t_keys = key_sets(source), key_sets(target)
            if (
                "object" in s_keys
                and "object" not in t_keys
                and "person" in t_keys
                and "person" not in s_keys
            ) or (
                "object" in t_keys
                and "object" not in s_keys
                and "person" in s_keys
                and "person" not in t_keys
            ):
                item.update(
                    status="uncertain",
                    reason="Сопоставление ключа объекта с данными человека требует ручной проверки.",
                )
        if axis == "identity" and item["status"] == "review" and source and target:

            def has_person_key(entries):
                return any(
                    "person" in c["keys"] and "object" not in c["keys"] and not c["negated"]
                    for c in entries
                )

            if has_person_key(source) != has_person_key(target):
                item.update(
                    status="uncertain",
                    reason="Привязка группы к человеку явно указана только с одной стороны; необходимо подтвердить единицу учёта.",
                )
        if axis == "authority" and item["status"] == "review":

            def request_vs_act(first, second, other_text):
                return (
                    any(e["kind"] == "request" and e["explicit"] for e in first)
                    and not REQUEST.search(other_text)
                    and not CONDITION.search(other_text)
                    and not NEGATIVE.search(other_text)
                    and (any(e["kind"] == "decide" for e in second) or ENACT.search(other_text))
                )

            if request_vs_act(source, target, after) or request_vs_act(target, source, before):
                item.update(
                    status="uncertain",
                    reason="В другой версии обнаружено действие без подтверждённого права только на его инициирование.",
                )
        audit[axis] = item
    if (
        generated
        and (EFFORT.search(before) or EFFORT.search(after))
        and (DUTY.search(before) or DUTY.search(after))
    ):
        item = dict(status="review")
        if (
            bool(EFFORT.search(before)) != bool(EFFORT.search(after))
            and DUTY.search(before)
            and DUTY.search(after)
        ):
            same_remainder = " ".join(EFFORT.sub("", before).casefold().split()) == " ".join(
                EFFORT.sub("", after).casefold().split()
            )
            item.update(
                status="reject" if same_remainder else "review",
                reason="Нужно сверить обязанность предпринять попытку и обязанность достичь результата.",
            )
        audit["effort"] = item
    return audit


def check_frames(body, original, proposal, required, generated=False):
    raw = body.get("contract_checks")
    if not isinstance(raw, dict):
        raise ValueError("missing_contract_checks")
    axes = {}
    status, reason = "pass", ""
    for axis in ("authority", "identity", "effort") if generated else ("authority", "identity"):
        rows = raw.get(axis)
        if not isinstance(rows, list):
            raise ValueError("invalid_contract_checks")
        axes[axis] = rows
        if axis in required and not rows and status == "pass":
            status, reason = "uncertain", "Не разобрано отношение полномочий или идентичности записей."
        for row in rows:
            if not isinstance(row, dict) or "s" not in row or "t" not in row:
                raise ValueError("invalid_contract_frame")
            left, right = row["s"], row["t"]
            for frame, units in ((left, original), (right, proposal)):
                if frame is None:
                    continue
                if (
                    not isinstance(frame, dict)
                    or not isinstance(frame.get("unit"), str)
                    or frame["unit"] not in units
                ):
                    raise ValueError("unanchored_contract_unit")
                quote = frame.get("quote")
                if not isinstance(quote, str) or not quote.strip() or quote not in units[frame["unit"]]:
                    raise ValueError("unanchored_contract_evidence")
                if axis == "authority":
                    if (
                        frame.get("power") not in ("request", "decide", "conditional_decision", "other")
                        or frame.get("denied") not in ("effective", "not_effective", "unspecified")
                        or not all(
                            isinstance(frame.get(k), str) and frame[k].strip()
                            for k in ("actor", "action")
                        )
                    ):
                        raise ValueError("invalid_authority_evidence")
                elif axis == "effort":
                    if frame.get("undertaking") not in (
                        "attempt",
                        "result",
                        "undetermined",
                    ) or frame.get("failed_after_attempt") not in ("complies", "breach", "undetermined"):
                        raise ValueError("invalid_effort_evidence")
                else:
                    keys = frame.get("keys")
                    if (
                        not isinstance(keys, list)
                        or any(k not in ("object", "person", "time", "channel", "other") for k in keys)
                        or not isinstance(frame.get("entity"), str)
                        or not frame["entity"].strip()
                        or any(
                            frame.get(k) not in ("one", "two", "unspecified")
                            for k in ("two_objects", "two_channels")
                        )
                    ):
                        raise ValueError("invalid_identity_evidence")
            if left is None and right is None:
                raise ValueError("empty_contract_frame")
            if left is None or right is None:
                return (
                    axes,
                    "reject",
                    "Полномочие или правило объединения появляется только в одной версии.",
                )
            if axis == "authority":
                facts = ("power", "denied")
                unresolved = left["power"] == "other" or left["denied"] == "unspecified"
            elif axis == "effort":
                facts = ("undertaking", "failed_after_attempt")
                unresolved = (
                    left["undertaking"] == "undetermined"
                    or left["failed_after_attempt"] == "undetermined"
                )
            else:
                facts = ("two_objects", "two_channels")
                if set(left["keys"]) != set(right["keys"]):
                    return axes, "reject", "Изменился обязательный состав ключа объединения записей."
                unresolved = (
                    not left["keys"] or "other" in left["keys"] or left["two_objects"] == "unspecified"
                )
            changed = [key for key in facts if left[key] != right[key]]
            unknown = {"other", "undetermined", "unspecified"}
            if changed and all(left[key] in unknown or right[key] in unknown for key in changed):
                status, reason = (
                    "uncertain",
                    "Одна из сторон не определяет последствия контрольной ситуации.",
                )
            elif changed:
                return (
                    axes,
                    "reject",
                    "У редакций разные последствия при отказе или разные результаты подсчёта в контрольной ситуации.",
                )
            if unresolved and axis in required and status == "pass":
                status, reason = (
                    "uncertain",
                    "Открытый вопрос о полномочиях или ключе учёта остался без проверяемого ответа.",
                )
        # An overall vote (or a frame labelled "same" on both sides) cannot erase a local
        # positive observation about a particular participant or object. Validate each source side.
        for side, name in (("s", "source"), ("t", "proposal")):
            if axis not in required:
                continue
            units = original if side == "s" else proposal
            observations = required[axis].get(name, [])
            for local in observations:
                frames = [
                    r[side]
                    for r in rows
                    if r[side]
                    and (
                        local["quote"] in units[r[side]["unit"]]
                        or units[r[side]["unit"]] in local["quote"]
                    )
                ]
                if axis == "authority" and local["explicit"]:
                    consistent = any(
                        f["power"] == local["kind"]
                        and (REQUEST if local["kind"] == "request" else ENACT).search(f["quote"])
                        for f in frames
                    )
                elif (
                    axis == "identity"
                    and not local["negated"]
                    and len(local["keys"]) == len(local["matches"]) == 1
                ):
                    key, evidence = local["keys"][0], local["matches"][0]
                    consistent = any(
                        key in f["keys"] and evidence.casefold() in f["quote"].casefold() for f in frames
                    )
                else:
                    continue
                if not consistent and status == "pass":
                    status, reason = (
                        "uncertain",
                        "Цитата судьи не подтверждает извлечённое полномочие или основание учёта.",
                    )
    return axes, status, reason
