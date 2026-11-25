"""The private-chat adapter. Long polling starts only when run as a program."""

import asyncio
import os
import time
from pathlib import Path

import httpx

import preferences
from engine import analyze, finish
from formats import LIMIT
from policy import LENGTHS, STYLES, literal_terms
from report import audit_note, diagnostic_report, marked_pages
from personal_search import search as search_personal
from workspace import document_consent_required, document_detector, propose

HELP = """Пришлите черновик сообщением или файлом TXT, MD, HTML, DOCX, PPTX, PDF. Для вставки разметки выберите /format html или /format md. Обычный текст — /format txt.

До проверки: /context аудитория и цель, /source текст требований.
Для замены: /style voice|direct|formal|friendly, /precision minimal|free|literal, /length short|near|free|fit, /terms выражения через запятую.
Диагностика: /finding 1, /uncertain 1 (нужен контекст), /report (DOCX), /marked (пометки в PDF).
Замена: /rewrite 1,2 → /show 1 → /save 1,2. Команда /save принимает перечисленные замены и выгружает копию. /original выгружает исходник. /txt all — чистый текст.

Личная память: /example было | стало | пояснение, /except фраза | пояснение, /memory, /forget номер. Личные совпадения обозначены p1, p2. /rewrite p1 применяет точную пару локально. /share on разрешает передать модели сохранённые примеры и пояснения при следующей замене. /similar on отправляет модели текст и примеры для поиска похожих фраз. /clear удаляет текущий черновик."""


def indices(value, count, allow_all=False):
    if allow_all and value == "all":
        return list(range(count))
    if not value.strip():
        raise ValueError("Укажите номер через запятую.")
    try:
        rows = [int(s.strip()) - 1 for s in value.split(",")]
    except ValueError:
        raise ValueError("Нужны номера через запятую.") from None
    if any(not 0 <= i < count for i in rows):
        raise ValueError("Неизвестный номер.")
    return rows


class Bot:
    def __init__(self, token, client):
        self.url = "https://api.telegram.org/bot" + token + "/"
        self.files = "https://api.telegram.org/file/bot" + token + "/"
        self.client = client
        self.sessions = {}

    async def api(self, method, **kwargs):
        response = await self.client.post(self.url + method, **kwargs)
        response.raise_for_status()
        body = response.json()
        if not body["ok"]:
            raise ValueError("Telegram не принял запрос.")
        return body["result"]

    async def handle(self, m):
        if m["chat"]["type"] != "private":
            return
        chat = m["chat"]["id"]
        if m.get("from", {}).get("id", chat) != chat:
            return
        owner = "telegram:" + str(chat)
        now = time.monotonic()
        for c in list(self.sessions):
            if now - self.sessions[c]["touched"] > 1800:
                del self.sessions[c]
        if chat not in self.sessions and len(self.sessions) >= 32:
            del self.sessions[min(self.sessions, key=lambda c: self.sessions[c]["touched"])]
        s = self.sessions.setdefault(
            chat,
            dict(
                style="voice",
                length="short",
                precision="minimal",
                terms=[],
                source="",
                context="",
                format="txt",
                share=False,
                length_set=False,
            ),
        )
        s["touched"] = now

        async def say(text):
            # Telegram limits messages by UTF-16 code units. Preserve long diffs and rule lists.
            packet = []
            size = 0
            for char in text:
                width = 1 if ord(char) <= 0xFFFF else 2
                if size + width > 4000:
                    await self.api("sendMessage", json={"chat_id": chat, "text": "".join(packet)})
                    packet, size = [], 0
                packet.append(char)
                size += width
            if packet:
                await self.api("sendMessage", json={"chat_id": chat, "text": "".join(packet)})

        async def send(name, data):
            return await self.api(
                "sendDocument", data={"chat_id": str(chat)}, files={"document": (name, data)}
            )

        text = m.get("text", "")
        command, _, arg = text.partition(" ")
        arg = arg.strip()
        if command in ("/start", "/help"):
            extra = (
                "\nПо умолчанию работает локальная проверка. /models once — разрешить передачу следующего черновика, ТЗ и контекста настроенным моделям. /models off — отменить."
                if document_consent_required()
                else ""
            )
            await say(HELP + extra)
            return
        if command == "/clear":
            self.sessions.pop(chat, None)
            await say("Черновик удалён. Личные правила доступны через /memory.")
            return
        if command in ("/memory", "/example", "/except", "/forget"):
            if command == "/memory":
                rows = preferences.entries(owner)
                await say(
                    "\n".join(
                        f"{r['id']}. {r['before']} → {r['after'] or 'оставлять'} · {r['note']}"
                        for r in rows
                    )
                    or "Личных правил пока нет."
                )
                return
            if command == "/forget":
                try:
                    preferences.remove(owner, int(arg))
                except (KeyError, ValueError):
                    raise ValueError("Личная запись не найдена.") from None
            elif command == "/example":
                fields = [f.strip() for f in arg.split("|", 2)]
                if len(fields) < 2:
                    raise ValueError("Используйте /example было | стало | пояснение.")
                preferences.add(owner, "example", *fields)
            else:
                before, _, note = arg.partition("|")
                preferences.add(owner, "exception", before, note=note)
            if s.get("job"):
                s["job"] = preferences.attach(dict(s["job"], changes=[]), owner)
            await say("Личная память обновлена. /memory — посмотреть записи.")
            return
        if command == "/models":
            if arg not in {"once", "off"}:
                raise ValueError(
                    "Укажите /models once для следующего черновика или /models off для локальной проверки."
                )
            s["document_mode"] = "models" if arg == "once" else "local"
            await say(
                "Текст, ТЗ и контекст следующего черновика будут переданы настроенным моделям."
                if arg == "once"
                else "Следующий черновик будет проверен локально."
            )
            return
        if command == "/share":
            if arg not in {"on", "off"}:
                raise ValueError("Укажите /share on или /share off.")
            s["share"] = arg == "on"
            await say(
                "Модель получит примеры при следующей замене."
                if s["share"]
                else "Примеры остаются в локальной памяти."
            )
            return
        settings = {
            "/style": set(STYLES) | {"voice", "direct", "formal", "friendly", "casual", "neutral"},
            "/length": set(LENGTHS),
            "/precision": {"minimal", "free", "literal"},
            "/format": {"txt", "md", "html"},
        }
        if command in {*settings, "/terms", "/source", "/context"}:
            key = command[1:]
            if command in settings and arg not in settings[command]:
                await say("Доступные значения: " + ", ".join(sorted(settings[command])))
                return
            s[key] = literal_terms(arg) if key == "terms" else arg
            if key == "length":
                s["length_set"] = True
            if key in {"context", "format", "source"}:
                s.pop("job", None)
                s.pop("data", None)
                await say("Настройка сохранена. Пришлите черновик для проверки.")
            else:
                if s.get("job"):
                    s["job"] = dict(s["job"], **{key: s[key]}, changes=[])
                await say("Настройка применится к следующему предложению замены.")
            return
        j = s.get("job")
        if command == "/similar":
            if not j:
                await say("Сначала пришлите черновик.")
                return
            j = s["job"] = await search_personal(j, owner, consent=arg == "on")
            scan = j["personal_search"]
            lines = [
                f"Найдено смысловых совпадений: {len(j['personal_variants'])}. Запросов: {scan['calls']} из {scan['planned']}. Сбоев: {scan['errors']}."
            ]
            lines += [
                f"v{i}. «{m['quote']}» · {m['rationale']}" for i, m in enumerate(j["personal_variants"], 1)
            ]
            if j["personal_variants"]:
                lines.append("Для замены: /share on, затем /rewrite v1.")
            await say("\n".join(lines))
            return
        if command in {
            "/report",
            "/marked",
            "/original",
            "/finding",
            "/uncertain",
            "/rewrite",
            "/show",
            "/save",
            "/txt",
            "/annotate",
        }:
            if not j:
                await say("Сначала пришлите черновик.")
                return
            fs = j["findings"]
            by_block = {b["id"]: b for b in j["blocks"]}
            if command == "/report":
                await send(*diagnostic_report(j))
            elif command == "/marked":
                await send(*await asyncio.to_thread(marked_pages, j, s["data"]))
            elif command == "/original":
                await send(j["name"], s["data"])
            elif command in ("/finding", "/uncertain"):
                if command == "/uncertain":
                    fs = [f for f in j["verdicts"] if f["decision"] == "uncertain"]
                for n in indices(arg, len(fs)):
                    f = fs[n]
                    locations = {key: b["label"] for key, b in by_block.items()}
                    locations.update(brief="Бриф", source="ТЗ автора")
                    note = audit_note(f, locations)
                    await say(
                        f"{by_block[f['block_id']]['label']} · {'Нужен контекст' if f['decision'] == 'uncertain' else 'Замечание'}\n«{f['quote']}»\n{f['pattern']}\n{f['effect']}\n{f['normal']}"
                        + ("\n" + note if note else "")
                    )
            elif command == "/rewrite":
                ids = []
                for value in arg.split(","):
                    token = value.strip()
                    personal = token.startswith(("p", "v"))
                    items = (
                        j.get("personal_variants" if token.startswith("v") else "personal_matches", [])
                        if personal
                        else fs
                    )
                    n = indices(token[1:] if personal else token, len(items))[0]
                    ids.append(items[n]["id"])
                share, s["share"] = s["share"], False
                j = s["job"] = await propose(j, s["data"], ids, owner, share)
                details = "\n".join(
                    f"{by_block[f['block_id']]['label']}: {f['reason']}"
                    for f in j.get("refusals", [])
                    if f.get("block_id") in by_block
                )
                await say(
                    f"Предложено замен: {len(j['changes'])}. "
                    + (details or j.get("rewrite_summary", ""))
                    + "\n/show 1 — сравнить. /save 1,2 — принять и скачать. /txt all — чистый текст."
                )
                if j["changes"]:
                    await self.handle({"chat": m["chat"], "text": "/show 1"})
            elif command == "/show":
                for n in indices(arg, len(j["changes"])):
                    c = j["changes"][n]
                    b = by_block[c["id"]]
                    await say(
                        b["label"]
                        + "\nБыло: "
                        + b["text"]
                        + "\nСтало: "
                        + (c["after"] or "(удалить фрагмент)")
                        + "\n"
                        + c["reason"]
                    )
            else:
                ids = [j["changes"][n]["id"] for n in indices(arg, len(j["changes"]), allow_all=True)]
                mode = {"/txt": "text", "/annotate": "annotate"}.get(command, "native")
                await send(*await asyncio.to_thread(finish, j, s["data"], ids, mode))
            return
        if command.startswith("/"):
            await say("Команды редактора доступны по /help.")
            return
        if "document" in m:
            doc = m["document"]
            if doc.get("file_size", 0) > LIMIT:
                raise ValueError("Лимит файла 10 МБ.")
            meta = await self.api("getFile", json={"file_id": doc["file_id"]})
            response = await self.client.get(self.files + meta["file_path"])
            response.raise_for_status()
            name, data = Path(doc.get("file_name", "document")).name, response.content
        elif text.strip():
            name, data = "draft." + s["format"], text.encode()
        else:
            await say("Пришлите текст или файл.")
            return
        params = {k: s[k] for k in ("style", "length", "precision", "terms", "source", "context")}
        if name.lower().endswith(".pptx") and not s["length_set"]:
            params["length"] = "fit"
        mode = s.pop("document_mode", "")
        consent = mode == "models"
        params["detector"] = document_detector(mode, consent)
        j = preferences.attach(await analyze(name, data, **params), owner)
        j["document_model_consent"] = consent
        s.update(job=j, data=data)
        await say(
            f"{j['backend']}. Прочитано {j['seen']}/{j['total']}. Замечаний: {len(j['findings'])}. "
            + j["summary"][:800]
            + "\n/finding 1 — цитата и объяснение. /report — отчёт. /rewrite 1,2 — предложить замены."
        )
        unsure = sum(v.get("decision") == "uncertain" for v in j.get("verdicts", []))
        if unsure:
            await say(f"Нужен контекст автора: {unsure}. /uncertain 1 — посмотреть.")
        if j["findings"]:
            await self.handle({"chat": m["chat"], "text": "/finding 1"})
        if j["personal_matches"]:
            await say(
                "Совпадения с личными правилами:\n"
                + "\n".join(
                    f"p{i}. {by['quote']} → {by['after'] or 'оставлять'}"
                    for i, by in enumerate(j["personal_matches"], 1)
                )
                + "\n/rewrite p1 — предложить точную личную замену."
            )


async def main():
    async with httpx.AsyncClient(timeout=65) as client:
        bot = Bot(os.environ["TELEGRAM_BOT_TOKEN"], client)
        offset = 0
        while True:
            try:
                updates = await bot.api("getUpdates", json={"offset": offset, "timeout": 30})
            except httpx.HTTPError:
                await asyncio.sleep(3)
                continue
            for update in updates:
                if "message" in update:
                    try:
                        await bot.handle(update["message"])
                    except Exception as e:
                        message = str(e) if isinstance(e, ValueError) else "Не удалось обработать запрос."
                        await bot.api(
                            "sendMessage",
                            json={"chat_id": update["message"]["chat"]["id"], "text": message[:1000]},
                        )
                offset = update["update_id"] + 1


if __name__ == "__main__":
    asyncio.run(main())
