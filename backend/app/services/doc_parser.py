from pathlib import Path
from zipfile import ZipFile, BadZipFile
from docx import Document
from pypdf import PdfReader


def parse(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        if not path.read_bytes()[:1024].lstrip().startswith(b"%PDF-"):
            raise ValueError("PDF 文件头无效")
        text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    elif suffix == ".docx":
        with ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > 80 * 1024**2:
                raise ValueError("DOCX 解压后超过 80 MB")
        doc = Document(path)
        text = "\n".join(
            [p.text for p in doc.paragraphs]
            + [c.text for t in doc.tables for row in t.rows for c in row.cells]
        )
    elif suffix in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8-sig")
        if "\x00" in text:
            raise ValueError("不是 UTF-8 文本文档")
    else:
        raise ValueError("不支持的格式")
    if not text.strip():
        raise ValueError("无可解析文字；扫描件请上传文字版")
    return text.strip()


def split_chunks(text):
    chunks = []
    for start in range(0, len(text), 370):
        chunks.append(text[start : start + 450])
        if start + 450 >= len(text):
            break
    return chunks
