import asyncio,io,json,os,sys,tempfile,unittest
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
import httpx,pymupdf
from docx import Document
from pptx import Presentation
from pptx.util import Inches,Pt
from fastapi.testclient import TestClient
from web import app
from formats import load,export
from policy import check
from engine import DEMO,analyze,finish,rewrite
from bot import Bot

def sample(ext):
    if ext==".txt":return (DEMO+"\r\nХвост\r\n").encode("utf-8-sig")
    if ext==".md":return ("---\nkind: fixture\n---\n# Заголовок\n"+DEMO+"\n```\n"+DEMO+"\n```\n").encode()
    f=io.BytesIO()
    if ext==".docx":
        doc=Document();doc.add_paragraph(DEMO);p=doc.add_paragraph();p.add_run("Цена ");p.add_run("12").bold=True;doc.save(f)
    elif ext==".pptx":
        doc=Presentation();s=doc.slides.add_slide(doc.slide_layouts[6]);sh=s.shapes.add_textbox(Inches(.5), Inches(.5), Inches(8), Inches(2))
        r=sh.text_frame.paragraphs[0].add_run();r.text=DEMO;r.font.size=Pt(13);r.font.name="DejaVu Sans";doc.save(f)
    else:
        with pymupdf.open() as doc:
            p=doc.new_page(width=760,height=250)
            p.insert_text((24,50),DEMO,fontname="RU",fontfile=os.getenv("EDITOR_PDF_FONT","/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),fontsize=13)
            return doc.tobytes()
    return f.getvalue()

class Core(unittest.TestCase):
    def setUp(self):self.key=patch.dict(os.environ,{"LLM_API_KEY":""});self.key.start()
    def tearDown(self):self.key.stop()
    def test_formats(self):
        for ext in [".txt",".md",".docx",".pptx",".pdf"]:
            with self.subTest(ext=ext):
                data=sample(ext);name="sample"+ext
                base=asyncio.run(analyze(name,data,length="fit"));self.assertFalse(base["changes"]);self.assertEqual(len(base["findings"]),1)
                j=asyncio.run(rewrite(base,data,["0"]));self.assertEqual(len(j["changes"]),1)
                c=j["changes"][0];self.assertEqual(finish(j,data,[])[1],data)
                fn,raw=finish(j,data,[c["id"]]);self.assertEqual(fn,"edited"+ext)
                _,blocks=load(fn,raw);self.assertTrue(any(b["text"]==c["after"] for b in blocks))
                self.assertEqual([b["text"] for b in blocks if not b["edit"]],[b["text"] for b in j["blocks"] if not b["edit"]])
                if ext==".txt":self.assertTrue(raw.startswith(b"\xef\xbb\xbf"));self.assertIn(b"\r\n",raw)
                if ext==".pdf":
                    _,annot=finish(j,data,[c["id"]],"annotate")
                    with pymupdf.open(stream=annot,filetype="pdf") as d:self.assertEqual(next(d[0].annots()).info["content"],c["after"])
    def test_overflow(self):
        for ext in (".pdf",".pptx"):
            data=sample(ext);_,blocks=load("s"+ext,data)
            with self.assertRaises(ValueError):export("s"+ext,data,blocks,{"0":"Ш"*500},strict=True)
    def test_constraints(self):
        b={"text":"Не менее 24 часов","edit":True}
        self.assertTrue(check(b,"24 часа","free",[]))
        self.assertTrue(check({"text":"Кот","edit":True},"Коты","short",[]))
        self.assertTrue(check({"text":"Договор готов","edit":True},"Документ готов","free",["Договор"]))
    def test_web(self):
        client=TestClient(app);r=client.post("/review",data={"text":DEMO});self.assertEqual(r.status_code,200)
        j=r.json();self.assertEqual(j["seen"],1);self.assertEqual([x["tool"] for x in j["trace"]],["read_blocks","locate_finding"])
        self.assertEqual(j["changes"],[]);j=client.post("/rewrite/"+j["token"],json={"ids":["0"]}).json()
        route="/export/"+j["token"];self.assertEqual(client.post(route,json={"ids":["0"]}).content.decode(),j["changes"][0]["after"])
        self.assertEqual(client.post(route,json={"ids":["33"]}).status_code,422)
    def test_bot(self):
        calls=[]
        async def run():
            def respond(req):calls.append(req);return httpx.Response(200,json={"ok":True,"result":True})
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                bot=Bot("fake",client)
                for text in ["/style formal",DEMO,"/report","/rewrite 1","/save all"]:await bot.handle(dict(chat=dict(type="private",id=42),text=text))
                self.assertIn("В программу входит",bot.sessions[42]["job"]["changes"][0]["after"])
                self.assertTrue(calls[-1].url.path.endswith("/sendDocument"))
        asyncio.run(run())
if __name__=="__main__":unittest.main()

class Report(unittest.TestCase):
    def test_checker_only_report_and_preview(self):
        client=TestClient(app)
        with patch.dict(os.environ,{"LLM_API_KEY":""}):
            for ext in (".pdf",".pptx"):
                data=sample(ext)
                r=client.post("/review",files={"file":("source"+ext,data)},data={"length":"fit"});j=r.json()
                self.assertFalse(j["changes"]);self.assertEqual(j["findings"][0]["quote"],DEMO)
                report=client.get("/report/"+j["token"]+"/docx")
                self.assertTrue(any(DEMO in p.text for p in Document(io.BytesIO(report.content)).paragraphs))
                marked=client.get("/report/"+j["token"]+"/pdf")
                with pymupdf.open(stream=marked.content,filetype="pdf") as doc:self.assertTrue(list(doc[0].annots()))
                preview=client.post("/preview/"+j["token"]+"/0",json={"ids":[]})
                self.assertTrue(preview.content.startswith(b"\x89PNG"))
