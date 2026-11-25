"""Opt-in editing examples and exceptions belonging to a single browser or private chat."""

import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connection():
    state = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local/share"))
    path = Path(os.getenv("PO_DELU_MEMORY_DB", state / "po-delu/preferences.sqlite3"))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, timeout=10)
    try:
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS preferences (
            id INTEGER PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
            phrase TEXT NOT NULL, replacement TEXT NOT NULL, note TEXT NOT NULL,
            UNIQUE(owner, phrase))""")
        yield db
        db.commit()
    finally:
        db.close()


def browser_owner(credential):
    # A high-entropy host-only bearer cookie is the browser's local login.
    return "browser:" + hashlib.sha256(credential.encode()).hexdigest()


def browser_credential(value):
    if value and len(value) == 43 and all(c.isascii() and (c.isalnum() or c in "-_") for c in value):
        return value
    return secrets.token_urlsafe(32)


def entries(owner):
    with connection() as db:
        rows = db.execute(
            "SELECT id,kind,phrase AS before,replacement AS after,note FROM preferences WHERE owner=? ORDER BY id",
            (owner,),
        )
        return [dict(row) for row in rows]


def add(owner, kind, before, after="", note=""):
    before, after, note = before.strip(), after.strip(), note.strip()
    if (
        kind not in {"example", "exception"}
        or not before
        or any(len(s) > 1000 for s in (before, after, note))
    ):
        raise ValueError("Укажите фразу и до 1000 символов в каждом поле.")
    if kind == "example" and (not after or before == after):
        raise ValueError("Укажите предпочтительную формулировку, отличную от исходной.")
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        count = db.execute(
            "SELECT count(*) FROM preferences WHERE owner=? AND phrase<>?", (owner, before)
        ).fetchone()[0]
        if count >= 80:
            raise ValueError("В памяти можно хранить до 80 записей.")
        db.execute(
            """INSERT INTO preferences(owner,kind,phrase,replacement,note) VALUES(?,?,?,?,?)
            ON CONFLICT(owner,phrase) DO UPDATE SET kind=excluded.kind,replacement=excluded.replacement,note=excluded.note""",
            (owner, kind, before, after if kind == "example" else "", note),
        )
    return entries(owner)


def remove(owner, item_id):
    with connection() as db:
        changed = db.execute("DELETE FROM preferences WHERE owner=? AND id=?", (owner, item_id)).rowcount
        if not changed:
            raise KeyError(item_id)
    return entries(owner)


def attach(job, owner):
    rules = entries(owner)
    matches = []
    total = 0
    for block in job["blocks"]:
        if not block.get("editorial", True):
            continue
        for item in rules:
            phrase = item["before"]
            cursor = 0
            while (start := block["text"].find(phrase, cursor)) >= 0:
                cursor = start + len(phrase)
                total += 1
                if len(matches) < 250:
                    matches.append(
                        dict(
                            item,
                            rule_id=item["id"],
                            block_id=block["id"],
                            start=start,
                            end=cursor,
                            quote=phrase,
                            id=f"p/{item['id']}/{block['id']}/{start}",
                        )
                    )
    return dict(
        job,
        personal_matches=matches,
        personal_total=total,
        personal_example_count=sum(r["kind"] == "example" for r in rules),
    )
