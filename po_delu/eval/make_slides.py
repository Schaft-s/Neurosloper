from pathlib import Path
from pptx import Presentation
from pptx.util import Inches,Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
p=Presentation()
def slide(title,labels):
    s=p.slides.add_slide(p.slide_layouts[6])
    def box(x,y,w,h,text,color=None):
        sh=s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h)) if color else s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        if color:
            sh.fill.solid();sh.fill.fore_color.rgb=RGBColor.from_string(color)
        r=sh.text_frame.paragraphs[0].add_run()
        r.text=text;r.font.size=Pt(19);r.font.name="DejaVu Sans"
        r.font.color.rgb=RGBColor.from_string("FFFFFF" if color else "212B38")
    box(.4,.5,9,.9,title)
    for i,text in enumerate(labels):
        box(.5+3.08*i,2.5,2.9,2.3,text,"345270")
slide("Какой пропуск оформить?",["На один день","На неделю","На месяц"])
slide("Порядок действий при приёмке",["1. Осмотрите тару","2. Если цела — примите","3. Если разбита — верните"])
slide("Куда отнести заявление?",["Дневная смена: кабинет 2","Ночная смена: кабинет 4","Удалённо: через портал"])
p.save(Path(__file__).with_name("visual.pptx"))
