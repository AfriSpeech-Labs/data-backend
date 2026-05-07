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
