from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiError(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ApiError


class AccountOut(BaseModel):
    id: int
    username: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    account: AccountOut


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)


class ApiKeyOut(BaseModel):
    id: int
    account_id: int
    name: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class ApiKeyCreateResponse(BaseModel):
    api_key: ApiKeyOut
    plaintext_key: str


class ApiKeyRevokeResponse(BaseModel):
    api_key: ApiKeyOut


class SubjectCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)


class SubjectOut(BaseModel):
    id: int
    account_id: int
    display_name: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SubjectListResponse(BaseModel):
    subjects: list[SubjectOut]


class LocationCreateRequest(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9_]+$")
    display_name: str = Field(min_length=1, max_length=200)


class LocationImageOut(BaseModel):
    mime_type: str
    size_bytes: int
    sha256: str
    original_filename: str | None
    uploaded_at: datetime
    url: str


class LocationOut(BaseModel):
    id: int
    code: str
    display_name: str
    created_at: datetime
    image: LocationImageOut | None = None

    model_config = ConfigDict(from_attributes=True)


class LocationListResponse(BaseModel):
    locations: list[LocationOut]


class LocationCreateResponse(BaseModel):
    location: LocationOut


class EpisodeCreateRequest(BaseModel):
    subject_id: int
    location_id: int
    protocol_version: str = "v1"


class HealEpisodeRequest(BaseModel):
    healed_at: datetime | None = None


class RelapseEpisodeRequest(BaseModel):
    reported_at: datetime | None = None
    reason: str = Field(min_length=1, max_length=255)


class AdvanceEpisodeRequest(BaseModel):
    pass


class EpisodeOut(BaseModel):
    id: int
    subject_id: int
    location_id: int
    status: str
    current_phase_number: int
    phase_started_at: datetime
    phase_due_end_at: datetime | None
    protocol_version: str
    healed_at: datetime | None
    obsolete_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EpisodeResponse(BaseModel):
    episode: EpisodeOut


class EpisodeListResponse(BaseModel):
    episodes: list[EpisodeOut]


class ApplicationCreateRequest(BaseModel):
    episode_id: int
    applied_at: datetime | None = None
    treatment_type: str | None = None
    treatment_name: str | None = None
    quantity_text: str | None = None
    notes: str | None = None


class ApplicationUpdateRequest(BaseModel):
    applied_at: datetime | None = None
    treatment_type: str | None = None
    treatment_name: str | None = None
    quantity_text: str | None = None
    notes: str | None = None


class ApplicationVoidRequest(BaseModel):
    voided_at: datetime | None = None
    reason: str = Field(min_length=1, max_length=255)


class ApplicationOut(BaseModel):
    id: int
    episode_id: int
    applied_at: datetime
    treatment_type: str
    treatment_name: str | None
    quantity_text: str | None
    phase_number_snapshot: int
    is_voided: bool
    voided_at: datetime | None
    is_deleted: bool
    deleted_at: datetime | None
    notes: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApplicationResponse(BaseModel):
    application: ApplicationOut


class ApplicationListResponse(BaseModel):
    applications: list[ApplicationOut]


class EventOut(BaseModel):
    id: int
    event_uuid: str
    episode_id: int
    event_type: str
    actor_type: str
    actor_id: str | None
    occurred_at: datetime
    payload: dict
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EventListResponse(BaseModel):
    events: list[EventOut]


class TimelineResponse(BaseModel):
    timeline: list[EventOut]


class DueItem(BaseModel):
    episode_id: int
    subject_id: int
    location_id: int
    current_phase_number: int
    treatment_due_today: bool
    next_due_at: datetime | None
    last_application_at: datetime | None
    due_slot: str | None = None
    missed_slots_today: list[str] = Field(default_factory=list)
    applications_completed_today: int = 0
    applications_expected_today: int = 0


class DueListResponse(BaseModel):
    due: list[DueItem]


class ApiKeyListResponse(BaseModel):
    api_keys: list[ApiKeyOut]


class AdherenceDayOut(BaseModel):
    date: date
    episode_id: int
    subject_id: int
    location_id: int
    phase_number: int
    expected_applications: int
    completed_applications: int
    credited_applications: int
    status: str
    source: str
    calculated_at: datetime
    finalized_at: datetime | None


class AdherenceCalendarResponse(BaseModel):
    days: list[AdherenceDayOut]


class AdherenceSummaryResponse(BaseModel):
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    expected_applications: int
    completed_applications: int
    credited_applications: int
    adherence_score: float | None
    completed_days: int
    partial_days: int
    missed_days: int
    not_due_days: int
    future_days: int

    model_config = ConfigDict(populate_by_name=True)


class AdherenceSummaryMetrics(AdherenceSummaryResponse):
    pass


class EpisodeAdherenceResponse(BaseModel):
    episode_id: int
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    summary: AdherenceSummaryMetrics
    days: list[AdherenceDayOut]

    model_config = ConfigDict(populate_by_name=True)


class AdherenceRebuildRequest(BaseModel):
    episode_id: int | None = None
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    active_only: bool = True
    source: str = "rebuild"

    model_config = ConfigDict(populate_by_name=True)


class AdherenceRebuildResponse(BaseModel):
    episodes_processed: int
    rows_persisted: int


class ImportSummary(BaseModel):
    subjects: int
    locations: int
    episodes: int
    applications: int
    events: int
    adherence_days: int


class ImportResponse(BaseModel):
    imported: ImportSummary
