from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Document, DocumentChunk


def chunk_text(text: str, chunk_size: int = 700, overlap: int = 120) -> list[str]:
    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not cleaned:
        return []
    chunks = []
    start = 0
    while start < len(cleaned):
        end = start + chunk_size
        chunks.append(cleaned[start:end])
        start = max(end - overlap, end)
    return chunks


async def save_document(db: Session, app_id: str, file: UploadFile) -> Document:
    if not file.filename or not file.filename.lower().endswith((".txt", ".md")):
        raise ValueError("Only .txt and .md files are supported in the MVP.")

    settings = get_settings()
    app_dir = Path(settings.storage_dir) / "documents" / app_id
    app_dir.mkdir(parents=True, exist_ok=True)

    raw = await file.read()
    text = raw.decode("utf-8", errors="ignore")
    file_path = app_dir / f"{uuid4()}_{file.filename}"
    file_path.write_text(text, encoding="utf-8")

    document = Document(app_id=app_id, filename=file.filename, file_path=str(file_path), status="ready")
    db.add(document)
    db.flush()

    for index, chunk in enumerate(chunk_text(text)):
        db.add(
            DocumentChunk(
                document_id=document.id,
                chunk_index=index,
                content=chunk,
                metadata_json={"filename": file.filename},
            )
        )

    db.commit()
    db.refresh(document)
    return document


def list_documents(db: Session, app_id: str) -> list[Document]:
    return list(db.scalars(select(Document).where(Document.app_id == app_id).order_by(Document.created_at.desc())))


def retrieve_chunks(db: Session, app_id: str, query: str, limit: int = 3) -> list[dict]:
    rows = db.execute(
        select(DocumentChunk, Document.filename)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(Document.app_id == app_id)
    ).all()
    terms = [term.lower() for term in query.replace("？", " ").replace("?", " ").split() if term.strip()]
    scored = []
    for chunk, filename in rows:
        content_lower = chunk.content.lower()
        score = sum(content_lower.count(term) for term in terms)
        if score == 0:
            score = 1 if any(char in content_lower for char in query[:12]) else 0
        if score > 0:
            scored.append((score, chunk, filename))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "chunk_id": chunk.id,
            "filename": filename,
            "content": chunk.content,
            "score": score,
        }
        for score, chunk, filename in scored[:limit]
    ]
