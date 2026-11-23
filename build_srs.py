"""Render the project's SRS.md. Requires Python 3.10+, Pillow and python-docx."""
from pathlib import Path
from functools import lru_cache
import io
import math
import os
import re
import shutil
import subprocess
import tempfile
import warnings
from zipfile import ZipFile

from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "2026期中-24080214-赵赟天-需求规格说明文档.docx"
INK, BLUE, TEAL, BG = "#334155", "#4F46E5", "#0D9488", "#F8FAFC"
ICE, MINT, LINE = "#EEF2FF", "#E6F7F4", "#CBD5E1"
SCALE = 2
font_dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
             Path("/System/Library/Fonts"), Path("/usr/share/fonts"), Path.home()/".local/share/fonts"]
font_names = ["msyh.ttc", "simhei.ttf", "PingFang.ttc", "NotoSansCJK-Regular.ttc", "simsun.ttc"]
FONT = next((f for name in font_names for folder in font_dirs if folder.exists()
             for f in folder.rglob(name)), None)
if not FONT:
    raise RuntimeError("请安装支持中文的微软雅黑、黑体、苹方或 Noto Sans CJK。")


@lru_cache(None)
def face(size):
    return ImageFont.truetype(str(FONT), size * SCALE)


@lru_cache(None)
def check_glyph(ch):
    font = face(20)
    if bytes(font.getmask(ch)) == bytes(font.getmask("\U0010ffff")):
        raise ValueError(f"字体缺少字形 {ch!r}")


class Canvas:
    """Layout in a 1000-unit coordinate system, rendered at print resolution."""
    def __init__(self, height):
        self.im = Image.new("RGB", (1000*SCALE, height*SCALE), BG)
        self.draw = ImageDraw.Draw(self.im)

    def box(self, x, y, w, h, fill="white", edge=LINE, radius=10):
        self.draw.rounded_rectangle(tuple(round(v*SCALE) for v in (x,y,x+w,y+h)),
                                    radius*SCALE, fill=fill, outline=edge, width=2)

    def line(self, points, color=LINE, width=2):
        self.draw.line([(round(x*SCALE),round(y*SCALE)) for x,y in points],
                       fill=color, width=width*SCALE)

    def label(self, x,y,s,size=20,color=INK,center=False):
        for ch in set(s):
            if ord(ch) > 127:
                check_glyph(ch)
        self.draw.multiline_text((x*SCALE,y*SCALE),s,font=face(size),fill=color,
                                 anchor="mm" if center else "la",align="center" if center else "left",
                                 spacing=4*SCALE)

    def ellipse(self, x,y,rx,ry,fill,edge):
        self.draw.ellipse(tuple(round(a*SCALE) for a in (x-rx,y-ry,x+rx,y+ry)),
                          fill,edge,width=2*SCALE)

    def button(self,x,y,w,s,accent=BLUE):
        self.box(x,y,w,36,accent,accent,6)
        self.label(x+w/2,y+18,s,17,"white",True)

    def arrow(self,a,b,color=INK,dashed=False):
        x,y=a
        dx,dy=b[0]-x,b[1]-y
        length=math.hypot(dx,dy)
        ux,uy=dx/length,dy/length
        for i in range(0,math.ceil(length),12 if dashed else math.ceil(length)):
            j=min(i+7,length) if dashed else length
            self.line([(x+i*ux,y+i*uy),(x+j*ux,y+j*uy)],color)
        x,y=b
        self.line([(x-10*ux-5*uy,y-10*uy+5*ux),b,(x-10*ux+5*uy,y-10*uy-5*ux)],color)

    def save(self,path):
        self.im.save(path,dpi=(300,300))


def architecture():
    c=Canvas(370)
    c.box(255,18,490,55,BLUE,BLUE)
    c.label(500,45,"智溯费曼  FeynmanMind",27,"white",True)
    for x, left, right, title, nodes, accent in [
        (368,12,724,"学生学习端",["用户\n认证","资料解析\n混合记忆","左窗费曼\n反向讲解","右窗联动\n百科助教","知识图谱\n诊断报告","记忆档案\n导出"],BLUE),
        (861,744,978,"后台管理与接口",["全局教学\n薄弱点统计","开放 API\n数据导出"],TEAL)]:
        c.line([(500,73),(500,94),(x,94),(x,118)])
        c.box(x-105,118,210,51,accent,accent)
        c.label(x,144,title,23,"white",True)
        step=(right-left)/len(nodes)
        for k,label in enumerate(nodes):
            cx=left+(k+0.5)*step
            c.line([(x,169),(x,210),(cx,210),(cx,242)])
            c.box(cx-step/2+3,242,step-6,95,"white",accent)
            c.label(cx,289,label,20,INK,True)
    return c


def wrap_label(text,n):
    if len(text)<=n:
        return text
    mid=(len(text)+1)//2
    return text[:mid]+"\n"+text[mid:]


def uml(number,ascii_lines):
    actor=re.search(r"\[([^]]+)\]",ascii_lines).group(1)
    items=re.findall(r"\((U\d{4}):\s*([^)]+)\)",ascii_lines)
    inc=re.findall(r"<<include>>-->\s*\(([^)]+)\)",ascii_lines)
    parent=next((i for i,(uid,_) in enumerate(items)
                 if re.search(uid+r"[^\n]*<<include>>",ascii_lines)),len(items)-1)
    if number=="3.6":
        items=[("U0009","注册账号"),("U0009","登录与角色鉴权")]
        parent=1
        inc=["校验登录凭据","签发 JWT 令牌"]
    c=Canvas(345)
    c.box(145,15,838,312,"white",LINE)
    c.label(169,26,"FeynmanMind   /   系统边界",16,BLUE)
    c.ellipse(67,127,15,15,BG,INK)
    c.line([(67,142),(67,191),(48,216)],INK,3)
    c.line([(67,191),(86,216)],INK,3)
    c.line([(42,159),(92,159)],INK,3)
    c.label(67,251,actor,18,INK,True)
    ys=[171] if len(items)==1 else [105,243]
    targets={1:[ys[parent]],2:[111,244],3:[80,176,272]}[len(inc)]
    for (uid,title),y in zip(items,ys):
        c.line([(92,159),(190,y)],INK)
        c.ellipse(345,y,155,59,ICE,BLUE)
        c.label(345,y,uid+"\n"+wrap_label(title,13),18,INK,True)
    for name,y in zip(inc,targets):
        start=(498,ys[parent])
        end=(697,y)
        c.arrow(start,end,TEAL,True)
        mid=(start[1]+y)/2
        c.box(535,mid-18,123,25,"white","white",0)
        c.label(596,mid-6,"<<include>>",17,TEAL,True)
        c.ellipse(830,y,132,39,MINT,TEAL)
        c.label(830,y,wrap_label(name,12),18,INK,True)
    return c


def prototype(number):
    c=Canvas(475)
    titles=["学习资料库","双窗教学工作台","学习诊断","后台数据中心"]
    c.box(1,1,998,473,BG,LINE)
    c.box(1,1,998,56,"#253355","#253355",7)
    c.label(22,14,"智溯费曼   /   "+titles[number-1],22,"white")
    nav=["资料库   工作台   图谱   报告   后台","机器学习基础   /   同窗模式   /   总掌握度 72%",
         "动态知识图谱   /   费曼诊断报告","导出 JSON    下载 SQLite    OpenAPI /docs"][number-1]
    c.label(990-c.draw.textlength(nav,font=face(18))/SCALE-14,16,nav,18,"white")
    if number==1:
        c.box(18,77,268,375,"white")
        c.label(36,95,"资料概览",23,BLUE)
        c.label(36,154,"4 份文档   ·   56 个节点",18)
        c.label(36,197,"平均掌握率   68%",21,TEAL)
        c.line([(36,251),(263,251)])
        c.label(36,274,"记忆档案",22,BLUE)
        c.label(36,318,"核心概念树  /  编辑备注",18)
        c.button(36,385,100,"导出 MD")
        c.button(147,385,112,"导出 JSON",TEAL)
        c.box(308,80,316,45)
        c.label(324,90,"搜索资料…",18)
        c.label(649,92,"学科筛选",18)
        c.button(824,85,150,"上传资料")
        for x,title,n,rate in [(310,"机器学习基础",18,.72),(653,"计算机网络",22,.42)]:
            c.box(x,155,320,288)
            c.label(x+18,178,title,25)
            c.label(x+18,237,f"{n} 个知识点   ·   解析完成",18,TEAL)
            c.label(x+18,294,"掌握度",16)
            c.box(x+18,327,278,9,ICE,ICE,4)
            c.box(x+18,327,278*rate,9,BLUE,BLUE,4)
            c.button(x+18,378,84,"开讲")
            c.button(x+118,378,130,"记忆档案",TEAL)
    elif number==2:
        for x,w,title,accent,tint in [(12,534,"费曼听众   /   55%",BLUE,ICE),(552,436,"溯源助教   /   45%",TEAL,MINT)]:
            c.box(x,70,w,392)
            c.box(x,70,w,46,tint,tint)
            c.label(x+15,80,title,22,accent)
        c.label(29,124,"听众：小学生  /  同窗  /  考官",18)
        c.label(29,155,"已掌握 4    待巩固 3    薄弱 2",16,TEAL)
        c.box(28,192,485,62,ICE,ICE)
        c.label(42,202,"听众：如果学习率特别大，会发生什么？",20)
        c.button(30,267,210,"去右侧查证此问题")
        c.label(30,324,"我：像下山时步子太大，直接跨过山谷。",19)
        c.label(30,365,"通俗准确   +18 分",17,TEAL)
        c.label(570,128,"优先溯源当前资料",18,TEAL)
        c.label(570,177,"查证：学习率过大",21)
        c.label(570,223,"参数可能在极小值附近震荡或发散。",18)
        c.label(570,270,"引用：《机器学习基础》片段 #7",18,TEAL)
        c.label(570,305,"关联前置：偏导数、梯度",17)
        c.button(569,359,208,"回左窗继续讲解",TEAL)
        for x,w,msg in [(30,497,"输入讲解内容…                         发送"),(569,399,"向百科助教提问…                   提问")]:
            c.box(x,413,w,36)
            c.label(x+10,419,msg,16)
    elif number==3:
        c.box(14,75,508,385)
        c.box(535,75,451,385)
        c.label(30,90,"知识记忆图谱",23,BLUE)
        c.label(553,90,"四维诊断报告",23,TEAL)
        for a,b in [((145,247),(231,178)),((161,260),(342,260))]:
            c.arrow(a,b,INK)
        for x,y,t,color in [(106,258,"梯度下降\n90%","#C8F1DE"),(272,157,"过拟合\n25%","#FFE1DF"),(410,260,"正则化\n60%","#FDE9A9")]:
            c.ellipse(x,y,60,37,color,INK)
            c.label(x,y,t,17,INK,True)
        c.label(155,180,"对比",15)
        c.label(229,231,"前置",15)
        c.label(30,330,"节点详情   /   标准定义与薄弱记录",18)
        c.button(30,397,217,"发起专项费曼考核")
        cx,cy=759,218
        for r in [18,36,54,72]:
            pts=[(cx,cy-r),(cx+r,cy),(cx,cy+r),(cx-r,cy)]
            c.line(pts+[pts[0]],LINE,1)
        pts=[(cx,cy-72*.88),(cx+72*.82,cy),(cx,cy+72*.91),(cx-72*.79,cy)]
        c.draw.polygon([(int(x*SCALE),int(y*SCALE)) for x,y in pts],fill="#BCE7E1")
        c.line(pts+[pts[0]],TEAL,3)
        for x,y,t in [(759,133,"覆盖度 88"),(890,218,"准确度 82"),(759,307,"通俗度 91"),(625,218,"逻辑性 79")]:
            c.label(x,y,t,16,INK,True)
        c.label(555,333,"薄弱点：L1/L2 稀疏性差异、学习率衰减策略",17)
        c.button(554,397,188,"导出 Markdown",TEAL)
        c.button(756,397,134,"保存 PDF")
    else:
        for i,(t,v) in enumerate(zip(["总用户","总文档","总知识点","总会话","平均掌握"],["12","18","245","46","71%"])):
            x=17+195*i
            c.box(x,77,185,90)
            c.label(x+12,84,t,17)
            c.label(x+12,115,v,30,TEAL if i==4 else BLUE)
        c.box(17,187,470,266)
        c.box(501,187,480,266)
        c.label(35,202,"高频薄弱知识点   /   Top 10",21,BLUE)
        for i,(name,n) in enumerate([("反向传播链式法则",14),("过拟合与正则化",11),("注意力机制缩放",9)]):
            y=252+56*i
            c.label(34,y,name,17)
            c.box(184,y+4,n*15,16,TEAL,TEAL,4)
            c.label(192+n*15,y,str(n),16)
        c.label(519,202,"数据表审阅",22,TEAL)
        c.label(519,248,"文档    节点    日志    报告",18)
        c.box(519,289,330,38)
        c.label(531,295,"搜索记录…",16)
        c.button(861,290,100,"刷新",TEAL)
        c.box(519,349,443,38,ICE,ICE)
        c.label(529,355,"ID      名称          状态       更新       操作",17)
        c.label(529,402,"01      机器学习     完成       今日       删除",16)
    return c

def element(parent, tag, **attrs):
    node=OxmlElement("w:"+tag)
    for key,val in attrs.items():
        node.set(qn("w:"+key),str(val))
    parent.append(node)
    return node


def field(p,code,display=""):
    element(p.add_run()._r,"fldChar",fldCharType="begin")
    element(p.add_run()._r,"instrText").text=" "+code+" "
    element(p.add_run()._r,"fldChar",fldCharType="separate")
    p.add_run(display)
    element(p.add_run()._r,"fldChar",fldCharType="end")


def typeface(obj, east="宋体",size=12,color="222D40",latin="Times New Roman"):
    obj.font.name=latin
    fonts=obj._element.get_or_add_rPr().rFonts
    for key in ("asciiTheme","hAnsiTheme","eastAsiaTheme"):
        fonts.attrib.pop(qn("w:"+key),None)
    fonts.set(qn("w:eastAsia"),east)
    obj.font.size=Pt(size)
    obj.font.color.rgb=RGBColor.from_string(color)


def compact(p,size_space=2):
    f=p.paragraph_format
    f.first_line_indent=Pt(0)
    f.space_before=Pt(0)
    f.space_after=Pt(size_space)
    f.line_spacing=1.14
    return p


def centered(doc,text="",size=12,color="334155",space=8,bold=False):
    p=compact(doc.add_paragraph(),space)
    p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    r=p.add_run(text)
    typeface(r,"黑体" if bold else "宋体",size,color)
    r.bold=bold
    return p


def rich(p,s,size=12,header=False):
    for part in re.split(r"(\*\*.*?\*\*|`[^`]+`)",s.replace("<br>","\n")):
        code=part.startswith(chr(96))
        bold=part.startswith("**")
        text=part.strip(chr(96)) if code else part[2:-2] if bold else part
        run=p.add_run(text)
        typeface(run,"黑体" if header else "宋体",size,"FFFFFF" if header else "283446",
                 "Consolas" if code else "Times New Roman")
        run.bold=bold or header


def table(doc,rows,widths):
    t=doc.add_table(rows=0,cols=len(widths))
    t.alignment=WD_TABLE_ALIGNMENT.CENTER
    t.autofit=False
    for col,w in zip(t.columns,widths):
        col.width=Cm(w)
    prop=t._tbl.tblPr
    borders=element(prop,"tblBorders")
    for edge in ("top","left","bottom","right","insideH","insideV"):
        element(borders,edge,val="single",sz=4,color="CBD5E1")
    pads=element(prop,"tblCellMar")
    for side,v in [("top",40),("bottom",40),("left",65),("right",65)]:
        element(pads,side,w=v,type="dxa")
    for i,data in enumerate(rows):
        if len(data)!=len(widths):
            raise ValueError(f"表格列数错误：{data}")
        row=t.add_row()
        pr=row._tr.get_or_add_trPr()
        element(pr,"cantSplit")
        if i==0:
            element(pr,"tblHeader",val="true")
        for j,(cell,s,w) in enumerate(zip(row.cells,data,widths)):
            cell.width=Cm(w)
            cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if not i or i%2==0:
                element(cell._tc.get_or_add_tcPr(),"shd",fill="1E3A8A" if not i else "F8FAFC")
            p=compact(cell.paragraphs[0],0)
            if i==0 or w<=1.6:
                p.alignment=WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.keep_with_next=i==0
            rich(p,s,10 if len(widths)>4 else 10.5,not i)
    return t


def setup(doc):
    sec=doc.sections[0]
    sec.page_width,sec.page_height=Cm(21),Cm(29.7)
    sec.left_margin=sec.right_margin=Cm(2.45)
    sec.top_margin,sec.bottom_margin=Cm(2.45),Cm(2.15)
    sec.header_distance=sec.footer_distance=Cm(1.05)
    for node in sec._sectPr.xpath("./w:docGrid"):
        sec._sectPr.remove(node)
    from docx.enum.style import WD_STYLE_TYPE
    for k in (1,2):
        name=f"TOC {k}"
        st=doc.styles[name] if name in doc.styles else doc.styles.add_style(name,WD_STYLE_TYPE.PARAGRAPH)
        typeface(st,"黑体" if k==1 else "宋体",11 if k==1 else 10.5)
        st.font.bold=k==1
        fmt=st.paragraph_format
        fmt.space_after=Pt(1)
        fmt.space_before=Pt(2 if k==1 else 0)
        fmt.first_line_indent=Pt(0)
        fmt.left_indent=Cm(0 if k==1 else .6)
        fmt.line_spacing=Pt(15)
    for border in doc.styles["Title"]._element.xpath("./w:pPr/w:pBdr"):
        border.getparent().remove(border)
    normal=doc.styles["Normal"]
    typeface(normal)
    fmt=normal.paragraph_format
    fmt.line_spacing=1.5
    fmt.first_line_indent=Pt(24)
    fmt.space_after=Pt(4)
    for level,size in enumerate([16,14,12],1):
        h=doc.styles[f"Heading {level}"]
        typeface(h,"黑体",size,"1E3A8A" if level==1 else "373B83")
        h.font.bold=True
        f=h.paragraph_format
        f.first_line_indent=Pt(0)
        f.space_before=Pt(13)
        f.space_after=Pt(6)
        f.keep_with_next=True
        f.page_break_before=level==1
    doc.core_properties.title="智溯费曼（FeynmanMind）软件需求规格说明文档"
    doc.core_properties.author="赵赟天、李金航、李承岩"


def front(doc,src,body):
    meta=dict(re.findall(r"^\*\*(.*?)\*\*：(.*)$",src,re.M))
    project,subtitle=meta["项目名称"].strip().split("——",1)
    centered(doc,"软件工程课程期中大作业",15,"4F46E5",28).paragraph_format.space_before=Pt(42)
    centered(doc,project,25,"1E3A8A",8,True)
    title=centered(doc,"软件需求规格说明文档",30,"182436",18,True)
    title.style=doc.styles["Title"]
    centered(doc,subtitle,14,"334155",24)
    centered(doc,"文档编号   "+meta["文档编号"].strip()+"      版本   "+meta["文档版本"].strip(),12)
    centered(doc,"编写日期   "+meta["编写日期"].strip(),12,space=36)
    centered(doc,"项目组成员分工表",14,"1E3A8A",10,True)
    team=[["角色","姓名","学号","主要职责"]]
    for (role,duty),person,num in zip(re.findall(r"^\| (组[长员]) \|.*?\| ([^|]+) \|$",src,re.M),
                                       ["赵赟天","李金航","李承岩"],["24080214","24080218","24080217"]):
        team.append([role,person,num,duty])
    assert len(team)==4
    table(doc,team,[1.6,2.2,3,8.6])
    doc.add_page_break()
    centered(doc,"目   录",19,"1E3A8A",12,True)
    # A single TOC field with real cached paragraphs; Word replaces them on update.
    headings=re.findall(r"^(#{2,3}) (.+)$",body,re.M)
    for i,(marks,title) in enumerate(headings):
        p=compact(doc.add_paragraph(),1)
        p.paragraph_format.left_indent=Cm(.6 if len(marks)==3 else 0)
        if not i:
            element(p.add_run()._r,"fldChar",fldCharType="begin")
            element(p.add_run()._r,"instrText").text=' TOC \\o "1-2" \\h \\z \\u '
            element(p.add_run()._r,"fldChar",fldCharType="separate")
        r=p.add_run(title)
        typeface(r,"宋体",10.5,"1E3A8A" if len(marks)==2 else "334155")
        r.bold=len(marks)==2
    element(p.add_run()._r,"fldChar",fldCharType="end")
    sec=doc.add_section(WD_SECTION.NEW_PAGE)
    sec.header.is_linked_to_previous=sec.footer.is_linked_to_previous=False
    p=compact(sec.header.paragraphs[0])
    p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    typeface(p.add_run(doc.core_properties.title),"宋体",9,"475569")
    element(element(p._p.get_or_add_pPr(),"pBdr"),"bottom",val="single",sz=5,space=3,color="CBD5E1")
    element(sec._sectPr,"pgNumType",start=1)
    p=compact(sec.footer.paragraphs[0])
    p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    p.add_run("第 ")
    field(p,"PAGE","1")
    p.add_run(" 页 / 共 ")
    field(p,"SECTIONPAGES","1")
    p.add_run(" 页")


def parse_table(lines):
    return [[s.strip() for s in line.strip().strip("|").split("|")]
            for line in lines if not re.fullmatch(r"[|:\s-]+",line)]


def build(figdir):
    src=(ROOT/"SRS.md").read_text(encoding="utf-8-sig")
    body=src[src.index("## 第1章"):]
    doc=Document()
    setup(doc)
    front(doc,src,body)
    # Replace only the indented source block directly preceding each caption.
    diagrams={}
    def replace(match):
        block,cap,num=match.groups()
        diagrams[num]=block
        return cap+"\n"
    body=re.sub(r"(?m)^((?:[ \t]+[^\n]*\n)+)\s*^(图 (\d+\.\d+) [^\n]+)\n",replace,body)
    assert len(diagrams)==11,diagrams.keys()
    lines=body.splitlines()
    expected=[]
    i=0
    while i<len(lines):
        raw=lines[i]
        s=raw.strip()
        i+=1
        if not s or s=="---" or s.startswith("**用例图（") or s=="**用例规约表**：":
            continue
        if s.startswith("#"):
            marks,title=s.split(" ",1)
            p=doc.add_heading(title,level=len(marks)-1)
            if title.startswith("第1章"):
                p.paragraph_format.page_break_before=False
        elif s.startswith("|"):
            start=i-1
            while i<len(lines) and lines[i].startswith("|"):
                i+=1
            rows=parse_table(lines[start:i])
            expected.append(rows)
            n=len(rows[0])
            widths={2:[2.65,13.45],3:[3.1,4.5,8.5],5:[1,6.7,1.65,4.1,2.65],6:[1,2.4,3.6,3.1,1.5,4.5]}[n]
            if rows[0]==["项目","内容"]:
                p=centered(doc,rows[1][1]+"   "+rows[2][1]+"用例规约",11,"1E3A8A",4,True)
                p.paragraph_format.space_before=Pt(8)
                p.paragraph_format.keep_with_next=True
            table(doc,rows,widths)
        elif re.match(r"图 \d+\.\d+ ",s):
            num=s.split()[1]
            c=architecture() if num=="1.1" else uml(num,diagrams[num]) if num.startswith("3.") else prototype(int(num[-1]))
            path=figdir/(num+".png")
            c.save(path)
            p=compact(doc.add_paragraph(),3)
            p.alignment=WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.keep_with_next=True
            shape=p.add_run().add_picture(str(path),width=Cm(16))
            shape._inline.docPr.set("descr",s)
            centered(doc,s,10.5,"334155",8)
        elif s.startswith("**表 "):
            title=s.replace("**","").replace(chr(96),"")
            p=centered(doc,title,11,"1E3A8A",5,True)
            p.paragraph_format.keep_with_next=True
        else:
            p=doc.add_paragraph()
            if s.startswith("- ") or re.match(r"\d+\. ",s):
                p.paragraph_format.first_line_indent=Pt(-12)
                p.paragraph_format.left_indent=Pt(36 if raw.startswith(" ") else 18)
                if s.startswith("- "):
                    s="•  "+s[2:]
            rich(p,s)
    doc.save(OUTPUT)
    audit(expected,figdir)


def audit(expected,figdir):
    d=Document(OUTPUT)
    assert len(d.inline_shapes)==11
    assert len([p for p in d.paragraphs if p.style.name=="Heading 1"])==6
    assert len(d.tables)==len(expected)+1
    for t,src_rows in zip(d.tables[1:],expected):
        actual=[[c.text for c in row.cells] for row in t.rows]
        target=[[s.replace("**","").replace(chr(96),"").replace("<br>","\n") for s in row] for row in src_rows]
        assert actual==target
        assert t.rows[0]._tr.xpath("./w:trPr/w:tblHeader")
        assert all(r._tr.xpath("./w:trPr/w:cantSplit") for r in t.rows)
    ids=[r[1][1] for r in expected if r[0]==["项目","内容"]]
    assert ids==[f"U{i:04}" for i in range(1,10)]
    assert sum("字段标识" in r[0] for r in expected)==9
    assert sum("接口路径" in r[0] for r in expected)==1
    assert sum("异常类型" in r[0] for r in expected)==1
    with ZipFile(OUTPUT) as z:
        media=[name for name in z.namelist() if name.startswith("word/media/")]
        assert len(media)==11
        pngs={p.read_bytes() for p in figdir.glob("*.png")}
        assert {z.read(m) for m in media}==pngs
        for m in media:
            im=Image.open(io.BytesIO(z.read(m)))
            assert all(abs(v-300)<1 for v in im.info["dpi"])
            im.verify()
    print("Verified 6 chapters, 9 use cases, 9 data tables, 1 API table, 1 fault table and 11 PNGs.")
    print("Figure font:",FONT)


def libreoffice():
    candidates=[shutil.which("soffice"),shutil.which("libreoffice"),
                "/Applications/LibreOffice.app/Contents/MacOS/soffice"]
    for var in ["ProgramFiles","ProgramFiles(x86)","LOCALAPPDATA"]:
        if var in os.environ:
            candidates.append(Path(os.environ[var])/"LibreOffice/program/soffice.exe")
    return next((str(p) for p in candidates if p and Path(p).is_file()),None)


if __name__=="__main__":
    with tempfile.TemporaryDirectory() as tmp:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            build(Path(tmp))
        if exe:=libreoffice():
            subprocess.run([exe,"-env:UserInstallation="+(Path(tmp)/"lo").as_uri(),
                            "--headless","--convert-to","doc:MS Word 97","--outdir",
                            str(ROOT),str(OUTPUT)],check=True,timeout=120)
            assert OUTPUT.with_suffix(".doc").is_file()
    if os.name=="nt" and shutil.which("powershell") and not libreoffice():
        # Use the installed Word layout engine to cache an accurate TOC.
        script=r"""
$ErrorActionPreference='Stop'
if (![type]::GetTypeFromProgID('Word.Application')) { exit 0 }
$w=New-Object -ComObject Word.Application
$w.Visible=$false
$w.DisplayAlerts=0
try {
 $d=$w.Documents.Open($env:SRS_WORD_PATH)
 $d.TablesOfContents.Item(1).Update()
foreach ($k in 1,2) {
  $st=$d.Styles.Item([int](-19-$k))
  $pf=$st.ParagraphFormat
  $pf.LineSpacingRule=4
  $pf.LineSpacing=16
  $pf.SpaceBefore=0
  $pf.SpaceAfter=1
  $pf.FirstLineIndent=0
  $pf.LeftIndent=($k-1)*17
  $st.Font.Size=11
}
 $d.Repaginate()
 $d.TablesOfContents.Item(1).UpdatePageNumbers()
 $d.Save()
} finally { if ($d) { $d.Close(0) }; $w.Quit() }
"""
        subprocess.run(["powershell","-NoProfile","-NonInteractive","-Command",script],
                       env={**os.environ,"SRS_WORD_PATH":str(OUTPUT)},check=True,timeout=120)
    print(OUTPUT)





