import logging
import re
import time
from pathlib import Path
from zipfile import ZipFile
from docx import Document
from pypdf import PdfReader


def parse(path):
    for attempt in range(3):
        try:
            return _read(path)
        except FileNotFoundError:
            raise
        except OSError as exc:
            if attempt == 2:
                raise
            logging.getLogger(__name__).warning(
                "读取资料遇到 %s，第 %d 次重试", type(exc).__name__, attempt + 1
            )
            time.sleep(0.1 * (attempt + 1))


def _read(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        if not path.read_bytes()[:1024].lstrip().startswith(b"%PDF-"):
            raise ValueError("PDF 文件头无效")
        text = "\n\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    elif suffix == ".docx":
        with ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > 80 * 1024**2:
                raise ValueError("DOCX 解压后超过 80 MB")
        doc = Document(path)
        text = "\n\n".join(
            [("## " if p.style and p.style.name.startswith("Heading") else "") + p.text
             for p in doc.paragraphs if p.text.strip()]
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
    """Keep sections and paragraphs separate; overlap only within a long block."""
    chunks = []
    heading = ""
    lines = []
    fence = ""

    def flush():
        nonlocal heading
        if not lines:
            return
        block = (heading + "\n" if heading else "") + "\n".join(lines).strip()
        for start in range(0, len(block), 370):
            chunks.append(block[start:start + 450])
            if start + 450 >= len(block):
                break
        lines.clear()
        heading = ""

    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker and not fence:
            fence = marker[1]
        elif fence and line.strip().startswith(fence):
            fence = ""
        elif not fence and re.match(r"^ {0,3}#{1,6}\s+", line):
            flush()
            heading = line.strip()
            continue
        elif not fence and not line.strip():
            flush()
            continue
        lines.append(line)
    flush()
    if not chunks and heading:
        chunks.append(heading)
    return chunks
