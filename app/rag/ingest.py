"""Ingest internal docs into pgvector.  python -m app.rag.ingest data/knowledge/*.md"""
import argparse
import asyncio
from pathlib import Path

from sqlalchemy import delete

from app.db.models import Base, KnowledgeChunk
from app.db.session import SessionLocal, engine
from app.llm.vertex import embed
from app.rag.chunker import chunk_text


async def ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def ingest_file(path: Path, batch: int = 32) -> int:
    chunks = chunk_text(path.read_text(encoding="utf-8"))
    async with SessionLocal() as session:
        await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source == path.name))
        for i in range(0, len(chunks), batch):
            texts = chunks[i:i + batch]
            vectors = await embed(texts, task_type="RETRIEVAL_DOCUMENT")
            session.add_all(KnowledgeChunk(source=path.name, chunk_index=i + j, content=t, embedding=v)
                            for j, (t, v) in enumerate(zip(texts, vectors)))
        await session.commit()
    return len(chunks)


async def main(files: list[Path]) -> None:
    await ensure_schema()
    for f in files:
        print(f"{f.name}: {await ingest_file(f)} chunks")
    await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    asyncio.run(main(ap.parse_args().files))