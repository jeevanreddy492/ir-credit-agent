from sqlalchemy import select

from app.db.models import KnowledgeChunk
from app.db.session import SessionLocal
from app.llm.vertex import embed


async def search(query: str, k: int = 4) -> list[dict]:
    [qvec] = await embed([query], task_type="RETRIEVAL_QUERY")
    distance = KnowledgeChunk.embedding.cosine_distance(qvec)
    stmt = select(KnowledgeChunk, distance.label("d")).order_by(distance).limit(k)
    async with SessionLocal() as session:
        rows = (await session.execute(stmt)).all()
    return [{"chunk_id": c.id, "source": c.source, "score": round(1.0 - float(d), 4), "content": c.content} for c, d in rows]
