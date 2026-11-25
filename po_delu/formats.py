"""Read and patch supported text while retaining original containers."""
import io
import re
import shutil
import os
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile
from lxml import etree as ET
from pptx import Presentation
import pymupdf as pdf

LIMIT = 10 * 1024 * 1024
EMU_PT = 12700
NS = {"w":"http://schemas.openxmlformats.org/wordprocessingml/2006/main",
      "a":"http://schemas.openxmlformats.org/drawingml/2006/main"}
XML = ET.XMLParser(resolve_entities=False, no_network=True)

def office(path, data, ext):
    blocks = []
    with ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist()) > 60 * 1024**2 or len(z.infolist()) > 3000:
            raise ValueError("Слишком большой распакованный документ")
        if any("vbaProject" in n or "/embeddings/" in n for n in z.namelist()):
            raise ValueError("Вложенные исполняемые объекты не поддерживаются")
        if ext == ".docx":
            parts = [("word/document.xml", "Документ")]
        else:
            deck = Presentation(io.BytesIO(data))
            parts = [(str(s.part.partname).lstrip("/"), f"Слайд {i+1}") for i,s in enumerate(deck.slides)]
        for page,(part,label) in enumerate(parts):
            root = ET.fromstring(z.read(part), parser=XML)
            prefix = "w" if ext == ".docx" else "a"
            paras = root.xpath(f".//{prefix}:p", namespaces=NS)
            for i,p in enumerate(paras):
                texts = p.xpath(f".//{prefix}:t", namespaces=NS)
                value = "".join(t.text or "" for t in texts)
                if not value.strip():
                    continue
                # Restrict writes to single-run paragraphs without fields, links or mixed content.
                allowed = {"p","pPr","r","rPr","t","endParaRPr"}
                runs=[t.getparent() for t in texts]
                def style_key(run):
                    props=run.find("{*}rPr")
                    if props is None:return b""
                    return ET.tostring(props,method="c14n",exclusive=True)
                simple = bool(runs) and all(ET.QName(node).localname in allowed
                    for node in [*p, *(child for run in runs for child in run)]) and all(
                    ET.QName(run).localname=="r" and len(run.findall("{*}t"))==1 for run in runs
                ) and len({style_key(run) for run in runs})==1 and not p.xpath(
                    ".//*[local-name()='hlinkClick' or local-name()='hyperlink' or local-name()='fldChar']")
                if ext==".docx" and p.xpath("ancestor::*[local-name()='del' or local-name()='ins' or local-name()='sdt' or local-name()='fldSimple']"):
                    simple=False
                block = dict(id=str(len(blocks)), text=value, label=f"{label} · абзац {i+1}",
                             edit=simple, part=part, para=i, page=page)
                if ext == ".pptx":
                    parent = texts[0]
                    shapes = parent.xpath("ancestor::*[local-name()='sp'][1]")
                    if shapes:
                        sp = shapes[0]
                        rect = sp.find("{*}spPr/{*}xfrm")
                        if rect is not None and not rect.get("rot") and not sp.xpath("ancestor::*[local-name()='grpSp']"):
                            off, size = rect.find("{*}off"), rect.find("{*}ext")
                            if off is not None and size is not None:
                                x,y,w,h=[int(v)/EMU_PT for v in (off.get("x"),off.get("y"),size.get("cx"),size.get("cy"))]
                                block["box"]=[x,y,x+w,y+h]
                        # Source IDs for grouping all paragraphs of a text box.
                        block["shape"]=sp.find("{*}nvSpPr/{*}cNvPr").get("id")
                blocks.append(block)
    return blocks

def load(name,data):
    ext = Path(name).suffix.lower()
    if len(data)>LIMIT:
        raise ValueError("Лимит файла 10 МБ")
    if ext in {".pptx",".docx"}:
        blocks=office(name,data,ext)
    elif ext in {".html", ".htm"}:
        from html_format import read_html
        blocks = read_html(data)
    elif ext in {".md",".txt"}:
        blocks=[]
        fence=None
        frontmatter=data.decode("utf-8-sig").splitlines()[0:1]==["---"]
        for i,line in enumerate(data.decode("utf-8-sig").splitlines(keepends=True)):
            s=line.rstrip("\r\n")
            if not s.strip():
                continue
            edit=True
            prefix=""
            editorial=True
            if ext==".md":
                lead=re.match(r"^( {0,3}(?:#{1,6}|[-+*]|[0-9]+[.)]|>) +)(.+)$",s)
                if lead and not fence and not frontmatter:
                    prefix,s=lead.groups()
                marker=re.match(r"^\s*(`{3,}|~{3,})",s)
                edit=not (fence or marker or re.search(r"[`~*_#>\[\]{}<>|\\]|^\s*(?:---|[-+=]|\d+\.)|^ {4}",s))
                editorial=not (fence or marker or frontmatter or s.startswith("    "))
                if frontmatter:
                    edit=False
                    if i and s.strip() in {"---","..."}:frontmatter=False
                if marker and not fence:
                    fence=marker[1]
                elif marker and fence and marker[1][0]==fence[0] and len(marker[1])>=len(fence):
                    fence=None
            blocks.append(dict(id=str(len(blocks)),text=s,edit=edit,label=f"Строка {i+1}",line=i,prefix=prefix,editorial=editorial))
    elif ext==".pdf":
        blocks=[]
        with pdf.open(stream=data,filetype="pdf") as doc:
            if doc.needs_pass or len(doc)>100:
                raise ValueError("PDF защищён или содержит более 100 страниц")
            for i,page in enumerate(doc):
                for b in page.get_text("blocks",sort=True):
                    if b[6]==0 and b[4].strip():
                        blocks.append(dict(id=str(len(blocks)),text=b[4].strip(),edit=True,
                            label=f"Страница {i+1} · блок {b[5]+1}",box=list(b[:4]),page=i))
        if not blocks:
            raise ValueError("Текстовый слой PDF не найден")
    else:
        raise ValueError("Поддерживаются TXT, MD, HTML, PDF, PPTX и DOCX")
    if not blocks or len(blocks)>500 or sum(len(b["text"]) for b in blocks)>100000:
        raise ValueError("Нужен текст: до 500 блоков и 100 000 символов")
    return ext,blocks

def render(data):
    exe=shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise ValueError("Для проверки слайда нужен LibreOffice")
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp)
        (base/"deck.pptx").write_bytes(data)
        subprocess.run([exe,"-env:UserInstallation="+(base/"profile").as_uri(),"--headless",
            "--convert-to","pdf","--outdir",tmp,str(base/"deck.pptx")],check=True,timeout=60,capture_output=True)
        return (base/"deck.pdf").read_bytes()

def verify_boxes(data,blocks,patches):
    with pdf.open(stream=render(data),filetype="pdf") as doc:
        for b in blocks:
            if b["id"] not in patches:
                continue
            if not b.get("box"):
                raise ValueError(f'{b["label"]}: границы фигуры неизвестны')
            page=doc[b["page"]]
            box=pdf.Rect(b["box"])
            if not page.rect.contains(box):
                raise ValueError("Фигура выходит за пределы слайда")
            words=[w[4] for w in page.get_text("words") if box.contains(pdf.Rect(w[:4]))]
            siblings=[s for s in blocks if s["page"]==b["page"] and s.get("shape")==b.get("shape")]
            expected="".join(patches.get(s["id"],s["text"]) for s in siblings)
            compact=lambda s:"".join(s.split())
            if compact(expected)!=compact("".join(words)):
                raise ValueError(f'{b["label"]}: текст не подтверждён внутри рамки. Сократите замену или проверьте слайд вручную.')

def rewrite_pdf(data,blocks,patches):
    """Replace isolated horizontal single-line spans on their original baseline."""
    font_path=os.getenv("EDITOR_PDF_FONT","/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not Path(font_path).is_file():
        raise ValueError("Для записи PDF нужен TTF со всеми символами замены")
    font=pdf.Font(fontfile=font_path)
    with pdf.open(stream=data,filetype="pdf") as doc:
        for page_no in sorted({b["page"] for b in blocks if b["id"] in patches}):
            page=doc[page_no]
            raw=page.get_text("dict")["blocks"]
            spans=[(line,sp) for rb in raw for line in rb.get("lines",[]) for sp in line["spans"]]
            actions=[]
            for b in blocks:
                if b["id"] not in patches or b["page"]!=page_no:continue
                box=pdf.Rect(b["box"])
                inside=[(line,sp) for line,sp in spans if box.intersects(pdf.Rect(sp["bbox"]))]
                if len(inside)!=1 or page.rotation or list(inside[0][0]["dir"])!=[1.0,0.0]:
                    raise ValueError("Для сложного блока PDF выберите комментарии или чистый текст")
                sp=inside[0][1]; new=patches[b["id"]]; size=sp["size"]; x,y=sp["origin"]
                if sp["text"].strip()!=b["text"] or any(not font.has_glyph(ord(c)) for c in new):
                    raise ValueError("Шрифт или область PDF требуют ручной проверки")
                if new and (x+font.text_length(new,fontsize=size)>box.x1+0.1 or
                    y-font.ascender*size<box.y0-1 or y-font.descender*size>box.y1+1):
                    raise ValueError("Замена не помещается в исходную строку PDF")
                if any(box.intersects(link["from"]) for link in page.get_links()):
                    raise ValueError("Область PDF содержит ссылку; выберите комментарии")
                actions.append((box,new,(x,y),size,pdf.sRGB_to_pdf(sp["color"])))
            for box,*_ in actions:page.add_redact_annot(box,fill=False)
            page.apply_redactions(images=0,graphics=0,text=0)
            for box,new,origin,size,color in actions:
                if new:page.insert_text(origin,new,fontname="EditorUnicode",fontfile=font_path,fontsize=size,color=color)
        return "edited.pdf",doc.tobytes(garbage=4,deflate=True)

def export(name,data,blocks,patches,mode="native",strict=False,*,spans=None):
    ext=Path(name).suffix.lower()
    if mode not in {"text", "native"} and not (mode == "annotate" and ext == ".pdf"):
        raise ValueError("Недоступный формат экспорта")
    selected=[b for b in blocks if b["id"] in patches]
    if mode=="text":
        return "edited.txt","\n\n".join(patches.get(b["id"],b["text"]) for b in blocks).encode()
    if not selected:
        return "edited"+ext,data
    if any(not b["edit"] for b in selected):
        raise ValueError("Есть блок только для чтения")
    if ext in {".html", ".htm"}:
        from html_format import patch_html
        return "edited"+ext, patch_html(data,blocks,patches,spans)
    if ext in {".md",".txt"}:
        bom=data.startswith(b"\xef\xbb\xbf")
        lines=data.decode("utf-8-sig").splitlines(keepends=True)
        for b in selected:
            line=lines[b["line"]]
            prefix=b.get("prefix","")
            lines[b["line"]]=prefix+patches[b["id"]]+line[len(prefix)+len(b["text"]):]
        return "edited"+ext,"".join(lines).encode("utf-8-sig" if bom else "utf-8")
    if ext==".pdf" and mode=="native":
        return rewrite_pdf(data,blocks,patches)
    if ext==".pdf":
        with pdf.open(stream=data,filetype="pdf") as doc:
            for b in selected:
                page=doc[b["page"]]
                ann=page.add_rect_annot(pdf.Rect(b["box"]))
                ann.set_info(title="По делу",content=patches[b["id"]]); ann.update()
            return "review.pdf",doc.tobytes()
    out=io.BytesIO()
    with ZipFile(io.BytesIO(data)) as src, ZipFile(out,"w") as dst:
        for item in src.infolist():
            raw=src.read(item)
            changes=[b for b in selected if b["part"]==item.filename]
            if changes:
                root=ET.fromstring(raw,parser=XML)
                prefix="w" if ext==".docx" else "a"
                paras=root.xpath(f".//{prefix}:p",namespaces=NS)
                for b in changes:
                    texts=paras[b["para"]].xpath(f".//{prefix}:t",namespaces=NS)
                    texts[0].text=patches[b["id"]]
                    for t in texts[1:]:t.text=""
                raw=ET.tostring(root,encoding="UTF-8",xml_declaration=True,standalone=True)
            dst.writestr(item,raw)
    result=out.getvalue()
    if ext==".pptx":
        verify_boxes(result,blocks,patches)
    return "edited"+ext,result
