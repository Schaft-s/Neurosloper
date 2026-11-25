"""Read UTF-8 HTML as passive text and patch source ranges without reserializing markup."""

import html
import re
from html.parser import HTMLParser

OMIT = set(
    "head script style template noscript svg math iframe object embed canvas pre code textarea title xmp plaintext noembed noframes".split()
)
VOID = set("area base br col embed hr img input link meta param source track wbr".split())
ENTITY = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);")
HIDDEN = re.compile(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", re.I)


class Reader(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.lines = [0, *(m.end() for m in re.finditer("\n", source))]
        self.stack = []
        self.blocks = []
        self.start = self.end = 0
        self.line = 1
        self.total = 0

    def source_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def flush(self):
        if self.end <= self.start:
            return
        raw = self.source[self.start : self.end]
        text = html.unescape(raw)
        if text.strip():
            self.total += len(text)
            if len(self.blocks) >= 500 or self.total > 100000:
                raise ValueError("Нужен текст до 500 блоков и 100 000 символов")
            # A replacement cannot safely split a partially decoded legacy character reference.
            mapped = ENTITY.sub(lambda m: html.unescape(m[0]), raw)
            self.blocks.append(
                dict(
                    id=str(len(self.blocks)),
                    text=text,
                    edit=mapped == text,
                    label=f"HTML · строка {self.line} · фрагмент {len(self.blocks) + 1}",
                    html_start=self.start,
                    html_end=self.end,
                )
            )
        self.start = self.end

    def handle_starttag(self, tag, attrs):
        self.flush()
        if tag in VOID:
            return
        attr = dict(attrs)
        hidden = tag in OMIT or "hidden" in attr or (attr.get("aria-hidden") or "").lower() == "true"
        hidden = hidden or bool(HIDDEN.search(attr.get("style") or ""))
        if len(self.stack) >= 1024:
            raise ValueError("Слишком большая глубина HTML")
        self.stack.append((tag, hidden or bool(self.stack and self.stack[-1][1])))

    def handle_startendtag(self, tag, attrs):
        # In HTML a trailing slash does not close normal elements.
        self.handle_starttag(tag, attrs)
        if tag in {"svg", "math"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self.flush()
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def collect(self, size):
        if self.stack and self.stack[-1][1]:
            return
        offset = self.source_offset()
        if self.end != offset or self.start == self.end:
            self.flush()
            self.start = offset
            self.line = self.getpos()[0]
        self.end = offset + size

    def handle_data(self, data):
        self.collect(len(data))

    def reference(self, text):
        start = self.source_offset()
        self.collect(len(text) + int(self.source.startswith(";", start + len(text))))

    def handle_charref(self, name):
        self.reference("&#" + name)

    def handle_entityref(self, name):
        self.reference("&" + name)


def read_html(data):
    reader = Reader(data.decode("utf-8-sig"))
    try:
        reader.feed(reader.source)
        reader.close()
        reader.flush()
    except (AssertionError, RecursionError):
        raise ValueError("Не удалось разобрать структуру HTML") from None
    return reader.blocks


def source_offsets(raw):
    """Map decoded boundaries, rejecting boundaries inside compound HTML entities."""
    offsets = [0]
    cursor = 0
    for match in ENTITY.finditer(raw):
        offsets.extend(range(cursor + 1, match.start() + 1))
        value = html.unescape(match[0])
        if value == match[0]:
            offsets.extend(range(match.start() + 1, match.end() + 1))
        elif value:
            offsets.extend([None] * (len(value) - 1) + [match.end()])
        else:
            raise ValueError("В изменяемом фрагменте есть пустая HTML-сущность")
        cursor = match.end()
    offsets.extend(range(cursor + 1, len(raw) + 1))
    return offsets


def replacements(before, after, spans):
    """Recover the changed payloads while preserving each intervening source range."""
    if not spans:
        start = 0
        while start < min(len(before), len(after)) and before[start] == after[start]:
            start += 1
        end, new_end = len(before), len(after)
        while end > start and new_end > start and before[end - 1] == after[new_end - 1]:
            end -= 1
            new_end -= 1
        return [(start, end, after[start:new_end])]
    intervals = []
    for s in sorted(spans, key=lambda s: s["start"]):
        a, b = s["start"], s["end"]
        if not 0 <= a < b <= len(before) or intervals and a < intervals[-1][1]:
            raise ValueError("Некорректные интервалы HTML")
        if intervals and intervals[-1][1] == a:
            intervals[-1] = (intervals[-1][0], b)
        else:
            intervals.append((a, b))
    position = intervals[0][0]
    suffix = before[intervals[-1][1] :]
    limit = len(after) - len(suffix)
    if not after.startswith(before[:position]) or not after.endswith(suffix) or limit < position:
        raise ValueError("Изменён HTML-текст вне выбранного интервала")
    parts = []
    for i, (a, b) in enumerate(intervals):
        if i + 1 < len(intervals):
            fixed = before[b : intervals[i + 1][0]]
            end = after.find(fixed, position, limit)
            if end == -1 or after.find(fixed, end + 1, limit) != -1:
                raise ValueError("Не удалось однозначно привязать несколько правок в HTML")
            parts.append((a, b, after[position:end]))
            position = end + len(fixed)
        else:
            parts.append((a, b, after[position:limit]))
    return parts


def patch_html(data, blocks, patches, spans):
    source = data.decode("utf-8-sig")
    out = []
    cursor = 0
    for block in blocks:
        key = block["id"]
        if key not in patches:
            continue
        left, right = block["html_start"], block["html_end"]
        raw = source[left:right]
        if left < cursor or html.unescape(raw) != block["text"]:
            raise ValueError("HTML изменился после проверки")
        offsets = source_offsets(raw)
        if len(offsets) != len(block["text"]) + 1:
            raise ValueError("Не удалось привязать HTML-сущности")
        for start, end, fragment in replacements(block["text"], patches[key], (spans or {}).get(key)):
            if offsets[start] is None or offsets[end] is None:
                raise ValueError("Интервал проходит внутри HTML-сущности")
            begin = left + offsets[start]
            out.extend((source[cursor:begin], html.escape(fragment, quote=False)))
            cursor = left + offsets[end]
    out.append(source[cursor:])
    return "".join(out).encode("utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8")
