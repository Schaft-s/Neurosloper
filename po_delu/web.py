import asyncio
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

import preferences
from engine import analyze, finish
from formats import LIMIT, render
from report import diagnostic_report, marked_pages
from personal_search import search as search_personal
from policy import literal_terms
from workspace import document_consent_required, document_detector, propose

app = FastAPI(title="По делу")
ROOT = Path(__file__).parent
COOKIE = "po_delu_browser"
gate = asyncio.Semaphore(2)


@dataclass
class Saved:
    owner: str
    created: float
    job: dict
    data: bytes


jobs: dict[str, Saved] = {}


@app.middleware("http")
async def browser_identity(request: Request, call_next):
    credential = preferences.browser_credential(request.cookies.get(COOKIE))
    request.state.owner = preferences.browser_owner(credential)
    response = await call_next(request)
    if credential != request.cookies.get(COOKIE):
        response.set_cookie(
            COOKIE,
            credential,
            max_age=365 * 86400,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    return response


def fetch_job(request, token):
    item = jobs.get(token)
    if not item or item.owner != request.state.owner or item.created < time.monotonic() - 1800:
        raise HTTPException(404, "Черновик недоступен или срок его хранения истёк.")
    return item


def save(owner, job, data):
    now = time.monotonic()
    for key in list(jobs):
        if jobs[key].created < now - 1800:
            del jobs[key]
    while len(jobs) >= 16:
        jobs.pop(next(iter(jobs)))
    token = secrets.token_urlsafe(32)
    jobs[token] = Saved(owner, now, job, data)
    return dict(job, token=token)


class Selection(BaseModel):
    ids: list[str] = Field(max_length=500)
    mode: str = "native"
    style: str | None = None
    length: str | None = None
    precision: str | None = None
    terms: list[str] | None = None
    share_examples: bool = False


class PersonalConsent(BaseModel):
    share_examples: bool = False


class Preference(BaseModel):
    kind: str
    before: str = Field(max_length=1000)
    after: str = Field(default="", max_length=1000)
    note: str = Field(default="", max_length=1000)


def error(exc, message):
    if isinstance(exc, UnicodeError):
        message = "Сохраните текстовый файл в UTF-8."
    elif isinstance(exc, ValueError):
        message = str(exc)
    return HTTPException(422, message)


def attachment(name, data):
    return Response(
        data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/", response_class=HTMLResponse)
async def home():
    controls = "" if document_consent_required() else "hidden disabled"
    return (ROOT / "index.html").read_text().replace("data-consent-visibility", controls)


@app.get("/editor.js")
async def editor_script():
    return Response((ROOT / "editor.js").read_bytes(), media_type="application/javascript")


@app.get("/editor.css")
async def editor_style():
    return Response((ROOT / "editor.css").read_bytes(), media_type="text/css")


@app.get("/preferences")
async def list_preferences(request: Request):
    return preferences.entries(request.state.owner)


@app.post("/preferences")
async def add_preference(request: Request, item: Preference):
    try:
        return preferences.add(request.state.owner, item.kind, item.before, item.after, item.note)
    except ValueError as e:
        raise error(e, "Не удалось сохранить запись.") from e


@app.delete("/preferences/{item_id}")
async def remove_preference(request: Request, item_id: int):
    try:
        return preferences.remove(request.state.owner, item_id)
    except KeyError:
        raise HTTPException(404, "Запись не найдена.") from None


@app.post("/review")
async def review(
    request: Request,
    text: str = Form(""),
    file: UploadFile | None = File(None),
    format: str = Form("txt"),
    style: str = Form("voice"),
    length: str = Form("short"),
    terms: str = Form(""),
    context: str = Form(""),
    source: str = Form(""),
    precision: str = Form("minimal"),
    exact_terms: str = Form(""),
    analysis_mode: str = Form(""),
    share_document: bool = Form(False),
):
    if file:
        data = await file.read(LIMIT + 1)
        name = Path(file.filename or "file").name
    else:
        if format not in {"txt", "md", "html"}:
            raise HTTPException(422, "Выберите формат текста.")
        data, name = text.encode(), "draft." + format
    try:
        async with gate:
            detector = document_detector(analysis_mode, share_document)
            literal = json.loads(exact_terms) if exact_terms else literal_terms(terms)
            if not isinstance(literal, list) or not all(isinstance(t, str) for t in literal):
                raise ValueError("Нужен список дословно защищённых выражений.")
            job = await analyze(
                name,
                data,
                style,
                length,
                literal,
                context,
                source=source,
                precision=precision,
                detector=detector,
            )
            job["document_model_consent"] = analysis_mode == "models" and share_document
        return save(request.state.owner, preferences.attach(job, request.state.owner), data)
    except Exception as e:
        raise error(e, "Не удалось разобрать файл или завершить проверку.") from e


@app.post("/personal-check/{token}")
async def personal_check(request: Request, token: str, consent: PersonalConsent):
    saved = fetch_job(request, token)
    try:
        async with gate:
            job = await search_personal(saved.job, saved.owner, consent=consent.share_examples)
        return save(saved.owner, job, saved.data)
    except Exception as e:
        raise error(e, "Не удалось проверить сходство с личными примерами.") from e


@app.post("/rewrite/{token}")
async def suggestions(request: Request, token: str, selection: Selection):
    saved = fetch_job(request, token)
    settings = dict(saved.job)
    for key in ("style", "length", "precision", "terms"):
        value = getattr(selection, key)
        if value is not None:
            settings[key] = value
    try:
        async with gate:
            job = await propose(settings, saved.data, selection.ids, saved.owner, selection.share_examples)
        return save(saved.owner, job, saved.data)
    except Exception as e:
        raise error(e, "Не удалось получить замену. Замечания сохранены.") from e


@app.post("/export/{token}")
async def download(request: Request, token: str, selection: Selection):
    saved = fetch_job(request, token)
    try:
        name, data = await asyncio.to_thread(finish, saved.job, saved.data, selection.ids, selection.mode)
    except Exception as e:
        raise error(e, "Не удалось собрать документ.") from e
    return attachment(name, data)


@app.post("/preview/{token}/{page}")
async def preview(request: Request, token: str, page: int, selection: Selection):
    saved = fetch_job(request, token)
    if saved.job["ext"] not in {".pptx", ".pdf"}:
        raise HTTPException(422, "Предпросмотр доступен для PDF и PPTX.")

    def image():
        name, data = finish(saved.job, saved.data, selection.ids)
        if name.endswith(".pptx"):
            data = render(data)
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if not 0 <= page < len(doc):
                raise ValueError("Нет такой страницы.")
            return doc[page].get_pixmap().tobytes("png")

    try:
        return Response(await asyncio.to_thread(image), media_type="image/png")
    except Exception as e:
        raise error(e, "Не удалось показать страницу.") from e


@app.get("/report/{token}/{kind}")
async def report(request: Request, token: str, kind: str):
    saved = fetch_job(request, token)
    try:
        if kind == "docx":
            name, data = await asyncio.to_thread(diagnostic_report, saved.job)
        elif kind == "pdf":
            name, data = await asyncio.to_thread(marked_pages, saved.job, saved.data)
        else:
            raise ValueError("Неизвестный формат отчёта.")
    except Exception as e:
        raise error(e, "Не удалось собрать отчёт.") from e
    return attachment(name, data)
