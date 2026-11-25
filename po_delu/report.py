"""Human-readable diagnosis, available before any rewriting is requested."""
import io
from docx import Document
import pymupdf
from formats import render

def diagnostic_report(job):
    doc=Document()
    doc.add_heading("Замечания к тексту",0)
    doc.add_paragraph(job["name"])
    doc.add_paragraph(f'{job["backend"]}. Посещено блоков: {job["seen"]} из {job["total"]}.')
    doc.add_paragraph(job["summary"])
    if not job["findings"]:doc.add_paragraph("За просмотренный объём замечаний не зарегистрировано.")
    for n,f in enumerate(job["findings"],1):
        b=job["blocks"][int(f["block_id"])]
        decision="Шаблонная формулировка" if f["decision"]=="flag" else "Нужен контекст"
        doc.add_heading(f'{n}. {b["label"]}',2)
        doc.add_paragraph(decision+" · "+f["pattern"])
        doc.add_paragraph("«"+f["quote"]+"»")
        doc.add_paragraph(f["effect"])
        doc.add_paragraph(f["normal"])
    raw=io.BytesIO();doc.save(raw)
    return "findings.docx",raw.getvalue()

def marked_pages(job,data):
    if job["ext"] not in {".pptx",".pdf"}:raise ValueError("Разметка страниц доступна для PPTX и PDF")
    source=render(data) if job["ext"]==".pptx" else data
    with pymupdf.open(stream=source,filetype="pdf") as doc:
        for f in job["findings"]:
            b=job["blocks"][int(f["block_id"])];page=doc[b["page"]]
            bounds=pymupdf.Rect(b["box"]) if b.get("box") else page.rect
            reason=f'{f["quote"]}\n{f["effect"]}\n{f["normal"]}'
            found=page.search_for(f["quote"],clip=bounds)
            if len(found)==1 and b["text"].count(f["quote"])==1:
                ann=page.add_highlight_annot(found[0])
            else:
                ann=page.add_text_annot(bounds.top_left,reason)
            ann.set_info(title=f["pattern"],content=reason)
            if f["decision"]=="uncertain":ann.set_colors(stroke=(1,.65,.2))
            ann.update()
        return "findings.pdf",doc.tobytes()
