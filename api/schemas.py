"""Response models. Only the fields listed here leave the API."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class DatesOut(BaseModel):
    dates: List[str]
    latest: Optional[str]


class TagOut(BaseModel):
    tag: str
    count: int


class TagsOut(BaseModel):
    tags: List[TagOut]


class ArchiveArticleOut(BaseModel):
    article_id: str
    rank: int
    title: Optional[str] = None
    title_en: Optional[str] = None
    title_zh: Optional[str] = None
    summary_en: Optional[str] = None
    summary_zh: Optional[str] = None
    long_summary_en: Optional[str] = None
    long_summary_zh: Optional[str] = None
    importance_score: Optional[int] = 0
    relevance_score: Optional[float] = 0
    source: Optional[str] = None
    category: Optional[str] = None
    published_at: Optional[str] = None
    url: Optional[str] = None
    labels: List[str] = Field(default_factory=list)


class DigestOut(BaseModel):
    date: str
    articles: List[ArchiveArticleOut]


class ArticleOut(BaseModel):
    id: str
    title: str
    title_en: Optional[str] = None
    title_zh: Optional[str] = None
    summary_en: Optional[str] = None
    summary_zh: Optional[str] = None
    long_summary_en: Optional[str] = None
    long_summary_zh: Optional[str] = None
    importance_score: Optional[int] = 0
    tags: List[str] = Field(default_factory=list)
    category: Optional[str] = None
    url: str
    source_name: Optional[str] = None
    published_at: Optional[datetime] = None


class ArticlesOut(BaseModel):
    articles: List[ArticleOut]
    total: int
