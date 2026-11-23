import hashlib
import logging
import math
import re
from functools import lru_cache
from collections import Counter
import unicodedata

import chromadb
from chromadb.config import Settings as ChromaSettings
from openai import OpenAI
from app.core.config import settings

BASE = "feynman_doc_chunks"
log = logging.getLogger(__name__)


@lru_cache
def client():
    return chromadb.PersistentClient(
        path=str(settings.data_dir / "chroma_db"),
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def collection(name=BASE):
    return client().get_or_create_collection(
        name, embedding_function=None, metadata={"hnsw:space": "cosine"}
    )


def lite(text):
    v = [0.0] * 384
    for word in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", text.lower()):
        terms = (
            [word]
            if word.isascii()
            else list(word) + [word[i : i + 2] for i in range(len(word) - 1)]
        )
        for term in terms:
            code = int.from_bytes(hashlib.blake2s(term.encode(), digest_size=4).digest(), "little")
            v[code % len(v)] += 1 if code & (1 << 31) else -1
    if not any(v):
        v[0] = 1
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


def api_name():
    key = (settings.embedding_base_url or settings.openai_base_url) + "|" + settings.embedding_model
    return BASE + "_" + hashlib.sha256(key.encode()).hexdigest()[:16]


def remote(texts):
    sdk = OpenAI(
        api_key=settings.embedding_api_key.get_secret_value()
        or settings.openai_api_key.get_secret_value(),
        base_url=settings.embedding_base_url or settings.openai_base_url,
        timeout=15,
        max_retries=1,
    )
    return [
        v.embedding
        for v in sorted(
            sdk.embeddings.create(model=settings.embedding_model, input=texts).data,
            key=lambda v: v.index,
        )
    ]


def remove(doc_id):
    for name in client().list_collections():
        name = getattr(name, "name", name)
        if name == BASE or name.startswith(BASE + "_"):
            client().get_collection(name, embedding_function=None).delete(where={"doc_id": doc_id})


def index(doc_id, title, chunks, *, strict_remote=False):
    use_api = settings.embedding_provider == "api"
    for start in range(0, len(chunks), 64):
        texts = chunks[start : start + 64]
        indices = range(start, start + len(texts))
        args = dict(
            ids=[f"doc_{doc_id}_c_{i}" for i in indices],
            documents=texts,
            metadatas=[{"doc_id": doc_id, "doc_title": title, "chunk_index": i} for i in indices],
        )
        collection().upsert(**args, embeddings=[lite(t) for t in texts])
        if use_api:
            try:
                collection(api_name()).upsert(**args, embeddings=remote(texts))
            except Exception:
                log.warning("远程嵌入不可用，本资料使用本地索引")
                collection(api_name()).delete(where={"doc_id": doc_id})
                if strict_remote:
                    raise
                use_api = False


def search(doc_id, query, top_k=4, *, strict_remote=False):
    if not 1 <= top_k <= 30:
        raise ValueError("top_k 范围为 1–30")
    col = collection()
    vec = lite(query)
    if settings.embedding_provider == "api":
        other = collection(api_name())
        if other.get(where={"doc_id": doc_id}, limit=1, include=[])["ids"]:
            try:
                vec = remote([query])[0]
                col = other
            except Exception:
                if strict_remote:
                    raise
                log.warning("查询嵌入使用本地兜底")
        elif strict_remote:
            raise RuntimeError("未找到远程向量索引")
    result = col.query(
        query_embeddings=[vec],
        where={"doc_id": doc_id},
        n_results=max(12, top_k * 3),
        include=["documents", "distances", "metadatas"],
    )
    semantic = {
        m["chunk_index"]: max(0.0, min(1.0, 1.0 - distance))
        for m, distance in zip(result["metadatas"][0], result["distances"][0])
    }

    # Lexical candidates come from the entire filtered document, not just vector hits.
    def pages():
        offset = 0
        while True:
            batch = collection().get(
                where={"doc_id": doc_id},
                limit=256,
                offset=offset,
                include=["documents", "metadatas"],
            )
            yield from zip(batch["metadatas"], batch["documents"])
            if len(batch["ids"]) < 256:
                return
            offset += len(batch["ids"])

    return hybrid_rank(query, pages(), semantic, top_k)


def normalize(text):
    return unicodedata.normalize("NFKC", text).casefold()


def lexical_terms(text):
    for word in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", normalize(text)):
        if word.isascii() or len(word) == 1:
            yield word
        else:
            yield from (word[i : i + 2] for i in range(len(word) - 1))


def hybrid_rank(query, chunks, semantic, top_k):
    terms = set(lexical_terms(query))
    phrase = "".join(normalize(query).split())
    candidates = []
    df = Counter()
    total = length_sum = 0
    for meta, text in chunks:
        words = list(lexical_terms(text))
        counts = Counter(w for w in words if w in terms)
        idx = meta["chunk_index"]
        total += 1
        length_sum += len(words)
        df.update(counts.keys())
        if counts or idx in semantic:
            exact = bool(phrase and phrase in "".join(normalize(text).split()))
            candidates.append((meta, text, len(words), counts, exact))
    avg = length_sum / max(total, 1) or 1
    idf = {t: math.log1p((total - n + 0.5) / (n + 0.5)) for t, n in df.items()}
    scores = []
    for meta, text, length, counts, exact in candidates:
        bm = sum(
            idf[t] * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / avg))
            for t, tf in counts.items()
        )
        lexical = bm / (bm + sum(idf.values()) or 1)
        idx = meta["chunk_index"]
        score = 0.35 * semantic.get(idx, 0) + 0.20 * lexical + 0.45 * exact
        heading = re.match(r"^#{1,6}\s+([^\n]+)", text)
        named = heading and "".join(normalize(heading[1]).split()) in phrase
        if named and not exact:
            score += 0.25
        if score < 0.12 and not (exact or named):
            continue
        scores.append(
            dict(doc_title=meta["doc_title"], chunk_index=idx, snippet=text, score=round(score, 5))
        )
    return sorted(scores, key=lambda hit: (-hit["score"], hit["chunk_index"]))[:top_k]
