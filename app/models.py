"""Pydantic models for API request/response."""
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Literal, Optional


class SpeechItemResponse(BaseModel):
    id: str
    length_english: float
    length_swahili: Optional[float] = None
    status: str  # "pending" | "submitted"
    text_english: Optional[str] = None  # source sentence (null for legacy bulk-upload)
    text_swahili: Optional[str] = None  # Reference from CSV or optional user submit


class PaginatedListResponse(BaseModel):
    items: list[SpeechItemResponse]
    total: int
    page: int
    limit: int


class BulkUploadResponse(BaseModel):
    created_ids: list[str] = Field(description="List of created document _ids")


class AudioStatsResponse(BaseModel):
    total: int
    submitted: int
    pending: int


class AudioDurationStatsResponse(BaseModel):
    """Aggregate WAV durations from stored per-item lengths (seconds)."""

    total_english_seconds: float
    total_english_submitted_seconds: float
    total_english_pending_seconds: float
    total_swahili_submitted_seconds: float
    total_english_hours: float
    total_english_submitted_hours: float
    total_english_pending_hours: float
    total_swahili_submitted_hours: float


class BulkCsvErrorItem(BaseModel):
    row: int
    detail: str


class BulkCsvResponse(BaseModel):
    created_ids: list[str]
    errors: list[BulkCsvErrorItem]
    processed: int
    failed: int


class MyCurrentBatchResponse(BaseModel):
    batch_index: int
    status: str
    submitted_count: int
    total: int


class CompletedBatchInfo(BaseModel):
    batch_index: int
    completed_at: datetime


class MyBatchesOverviewResponse(BaseModel):
    """Per-session: in-progress claimed batch (if any) and batches marked completed for this session."""

    current: Optional[MyCurrentBatchResponse] = None
    completed_batches: list[CompletedBatchInfo]


# --- Parallel synth pairs (bidirectional TTS + validation) ---


class SynthPairItemResponse(BaseModel):
    id: str
    length_english: float
    length_swahili: float
    text_english: Optional[str] = None
    text_swahili: Optional[str] = None
    validation_status: str  # pending | approved | rejected
    tts_english_voice: Optional[str] = None
    tts_english_engine: str = "edge"
    gender_en: Optional[str] = None
    tts_swahili_voice: Optional[str] = None
    tts_swahili_engine: str = "edge"
    gender_sw: Optional[str] = None


class SynthPairListResponse(BaseModel):
    items: list[SynthPairItemResponse]
    total: int
    page: int
    limit: int


class SynthPairStatsResponse(BaseModel):
    """Collection-wide sums for the synth pairs admin dashboard."""

    total: int
    total_english_seconds: float
    total_swahili_seconds: float


class SynthPairValidationBody(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = ""


# --- Parallel text corpus (text-only parallel lines per language pair) ---


class TextCorpusItemResponse(BaseModel):
    id: str
    language_pair_id: str
    text_source: str
    text_target: Optional[str] = None
    created_at: datetime
    source: Optional[str] = None
    task_type: Optional[str] = None
    status: Optional[str] = None
    source_language: Optional[str] = None
    target_language: Optional[str] = None
    submitted_at: Optional[datetime] = None


class TextCorpusListResponse(BaseModel):
    items: list[TextCorpusItemResponse]
    total: int
    page: int
    limit: int


class TextCorpusPairCount(BaseModel):
    pair_id: str
    count: int


class TextCorpusStatsResponse(BaseModel):
    """Global total and per-pair counts for parallel_text_corpus."""

    total: int
    by_pair: list[TextCorpusPairCount] = Field(default_factory=list)


class TextTranslationStatsResponse(BaseModel):
    """Counts for source-only text translation tasks."""

    total: int
    pending: int
    submitted: int


class TextTranslationItemResponse(BaseModel):
    id: str
    language_pair_id: str
    status: str
    text_source: str
    text_target: Optional[str] = None
    source_language: str = "english"
    target_language: str = "kom"
    created_at: datetime
    submitted_at: Optional[datetime] = None


class TextTranslationSubmitBody(BaseModel):
    text_source: str = Field(min_length=1)


class LeaderboardEntry(BaseModel):
    rank: int
    display_name: str
    submitted_count: int
    is_current_user: bool = False


class LeaderboardResponse(BaseModel):
    enabled: bool
    language_pair_label: str = ""
    entries: list[LeaderboardEntry] = Field(default_factory=list)


class AdminBatchClaimItem(BaseModel):
    workflow: Literal["audio", "text"]
    pair_id: str
    batch_index: int
    claimed_at: datetime
    contributor_user_id: Optional[str] = None
    contributor_phone: Optional[str] = None
    session_id: Optional[str] = None
    submitted_count: int
    total: int = 25
    status: str


class AdminBatchClaimsListResponse(BaseModel):
    items: list[AdminBatchClaimItem]


class LanguagePairDeleteCounts(BaseModel):
    speech_parallel: int = 0
    batch_claims: int = 0
    batch_completions: int = 0
    parallel_text_corpus: int = 0
    text_batch_claims: int = 0
    text_batch_completions: int = 0
    parallel_synth_pairs: int = 0
    gridfs_files: int = 0
    r2_objects: int = 0
    contributors: int = 0


class LanguagePairDeleteResponse(BaseModel):
    ok: bool = True
    pair_id: str
    slug: str
    clear_data: bool
    deleted: LanguagePairDeleteCounts
    language_pair_deleted: int = 0
