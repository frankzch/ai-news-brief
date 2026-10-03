from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from . import schemas
from .deps import ReadOnlyDB, get_db

router = APIRouter(prefix="/api")

DATE_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"


@router.get("/dates", response_model=schemas.DatesOut)
def dates(db: ReadOnlyDB = Depends(get_db)):
    found = db.get_archive_dates()
    return {"dates": found, "latest": found[0] if found else None}


@router.get("/tags", response_model=schemas.TagsOut)
def tags(db: ReadOnlyDB = Depends(get_db)):
    return {"tags": db.get_hot_tags().get("month", [])}


@router.get("/digest/{date}", response_model=schemas.DigestOut)
def digest(date: str = Path(pattern=DATE_PATTERN), db: ReadOnlyDB = Depends(get_db)):
    rows = db.get_daily_archive(date)
    if not rows:
        raise HTTPException(status_code=404, detail="No digest for this date")
    return {"date": date, "articles": rows}


@router.get("/articles", response_model=schemas.ArticlesOut)
def articles(
    category: Optional[Literal["news", "discussion", "video", "opensource"]] = None,
    tag: Optional[str] = Query(None, max_length=100),
    q: Optional[str] = Query(None, max_length=200),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: ReadOnlyDB = Depends(get_db),
):
    rows, total = db.search_articles(category=category, tag=tag, q=q, limit=limit, offset=offset)
    return {"articles": rows, "total": total}


@router.get("/articles/{article_id}", response_model=schemas.ArticleOut)
def article(article_id: str = Path(max_length=64), db: ReadOnlyDB = Depends(get_db)):
    row = db.get_article_by_id(article_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return row
