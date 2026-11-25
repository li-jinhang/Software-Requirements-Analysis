"""Shared ZIP limits and source handling for portable learning data."""

from pathlib import Path
from shutil import copyfileobj
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from app.core.config import settings
from app.services import rag_store

MAX_BACKUP = 512 * 1024**2
MAX_MANIFEST = 64 * 1024**2
MAX_SOURCE = 20 * 1024**2


def source_file(document):
    uploads = (settings.data_dir / "uploads").resolve()
    source = Path(document.file_path).resolve()
    return source if source.is_relative_to(uploads) and source.is_file() else None


def saved_chunks(doc_id):
    result = rag_store.collection().get(where={"doc_id": doc_id}, include=["documents", "metadatas"])
    return [text for _, text in sorted(zip(
        (meta["chunk_index"] for meta in result["metadatas"]), result["documents"]
    ))]


def verify_entries(archive, manifest, sources):
    infos = archive.infolist()
    expected = {manifest, *sources}
    if len(infos) != len(expected) or {info.filename for info in infos} != expected:
        raise ValueError("备份包含重复、缺失或无法识别的文件。")
    if sum(info.file_size for info in infos) > MAX_BACKUP or any(
        info.file_size > (MAX_MANIFEST if info.filename == manifest else MAX_SOURCE) for info in infos
    ):
        raise ValueError("备份超过文件大小限制。")


def write_archive(path, manifest, data, sources):
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(manifest, data)
        for name, source in sources.items():
            archive.write(source, name)
        verify_entries(archive, manifest, sources)
    if path.stat().st_size > MAX_BACKUP:
        raise ValueError("备份超过 512 MB。")


def restore_source(archive, entry, file_type, paths):
    path = settings.data_dir / "uploads" / (uuid4().hex + "." + file_type)
    if entry:
        with path.open("xb") as dest:
            paths.append(path)
            with archive.open(entry) as source:
                copyfileobj(source, dest)
    return str(path)
