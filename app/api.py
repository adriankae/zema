from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adherence import list_adherence_rows, persist_episode_adherence, rebuild_active_episode_adherence, summarize_adherence
from app.location_images import get_location_image_file, remove_location_image, store_location_image
from app.import_export import export_account_data, import_account_data
from app.dependencies import ActorContext, get_current_actor
from app.core.database import get_db
from app.core.time import utc_now
from app.schemas import (
    AccountOut,
    AdherenceCalendarResponse,
    AdherenceDayOut,
    AdherenceRebuildRequest,
    AdherenceRebuildResponse,
    AdherenceSummaryMetrics,
    AdherenceSummaryResponse,
    AdvanceEpisodeRequest,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyListResponse,
    ApiKeyRevokeResponse,
    ApplicationCreateRequest,
    ApplicationListResponse,
    ApplicationOut,
    ApplicationResponse,
    ApplicationUpdateRequest,
    ApplicationVoidRequest,
    DueListResponse,
    EpisodeCreateRequest,
    EpisodeListResponse,
    EpisodeResponse,
    EventListResponse,
    EpisodeAdherenceResponse,
    HealEpisodeRequest,
    ImportResponse,
    LoginRequest,
    LoginResponse,
    LocationCreateRequest,
    LocationCreateResponse,
    LocationImageOut,
    LocationListResponse,
    LocationOut,
    RelapseEpisodeRequest,
    SubjectCreateRequest,
    SubjectListResponse,
    SubjectOut,
    TimelineResponse,
)
from app.services import (
    advance_episode,
    authenticate_user,
    bootstrap_data,
    catch_up_episode_phases,
    create_api_key,
    create_episode,
    create_location,
    create_subject,
    delete_application,
    delete_subject,
    due_items,
    get_application,
    get_episode,
    get_last_successful_phase_catch_up,
    get_location,
    get_subject,
    issue_login_token,
    list_api_keys,
    list_applications,
    list_events,
    list_episodes,
    list_locations,
    list_subjects,
    log_application,
    relapse_episode,
    revoke_api_key,
    heal_episode,
    update_application,
    void_application,
)
from app.models import Account

router = APIRouter()


def _episode_response(episode) -> EpisodeResponse:
    return EpisodeResponse(episode=episode)


def _application_response(application) -> ApplicationResponse:
    return ApplicationResponse(application=application)


def _location_response(location) -> LocationOut:
    image = None
    if location.image_storage_key is not None:
        image = LocationImageOut(
            mime_type=location.image_mime_type,
            size_bytes=location.image_size_bytes,
            sha256=location.image_sha256,
            original_filename=location.image_original_filename,
            uploaded_at=location.image_uploaded_at,
            url=f"/locations/{location.id}/image",
        )
    return LocationOut(
        id=location.id,
        code=location.code,
        display_name=location.display_name,
        created_at=location.created_at,
        image=image,
    )


def _subject_response(subject) -> SubjectOut:
    return SubjectOut.model_validate(subject)


def _event_list(events) -> EventListResponse:
    return EventListResponse(events=events)


def _adherence_day_response(row) -> AdherenceDayOut:
    return AdherenceDayOut(
        date=row.date,
        episode_id=row.episode_id,
        subject_id=row.subject_id,
        location_id=row.location_id,
        phase_number=row.phase_number,
        expected_applications=row.expected_applications,
        completed_applications=row.completed_applications,
        credited_applications=row.credited_applications,
        status=row.status,
        source=getattr(row, "source", "calculated"),
        calculated_at=row.calculated_at,
        finalized_at=getattr(row, "finalized_at", None),
    )


def _adherence_summary_response(from_date: date, to_date: date, rows) -> AdherenceSummaryResponse:
    summary = summarize_adherence(rows)
    return AdherenceSummaryResponse(
        from_date=from_date,
        to_date=to_date,
        expected_applications=summary.expected_total,
        completed_applications=summary.completed_total,
        credited_applications=summary.credited_total,
        adherence_score=summary.adherence_score,
        completed_days=summary.completed_day_count,
        partial_days=summary.partial_day_count,
        missed_days=summary.missed_day_count,
        not_due_days=summary.not_due_day_count,
        future_days=summary.future_day_count,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    def handle_http_exception(_, exc: HTTPException):
        code_map = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            422: "invalid_request",
        }
        detail = exc.detail if isinstance(exc.detail, str) else "request failed"
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": code_map.get(exc.status_code, "invalid_request"), "message": detail}})

    @app.exception_handler(IntegrityError)
    def handle_integrity_error(_, exc: IntegrityError):
        return JSONResponse(status_code=409, content={"error": {"code": "conflict", "message": "request violates a database constraint"}})

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(_, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {"code": "invalid_request", "message": "request validation failed"}})


@router.get("/health")
def health() -> dict:
    payload = {"status": "ok"}
    last_catch_up = get_last_successful_phase_catch_up()
    if last_catch_up is not None:
        payload["phase_catch_up"] = last_catch_up
    return payload


@router.get("/")
def root() -> dict[str, str]:
    return {"service": "zema"}


@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    account = authenticate_user(db, payload.username, payload.password)
    return LoginResponse(access_token=issue_login_token(account), account=AccountOut.model_validate(account))


@router.get("/auth/me", response_model=AccountOut)
def me(actor: ActorContext = Depends(get_current_actor)):
    return AccountOut.model_validate(actor.account)


@router.get("/export")
def export_data(actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    payload = export_account_data(db, actor.account)
    filename = f"zema-export-{date.today().isoformat()}.json"
    return Response(
        json.dumps(payload, indent=2, sort_keys=True),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import", response_model=ImportResponse)
async def import_data(file: UploadFile = File(...), actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    if file.content_type not in {None, "", "application/json", "text/json"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="import file must be JSON")
    try:
        payload = json.loads((await file.read()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid import JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid import JSON")
    return ImportResponse(imported=import_account_data(db, actor.account, payload))


@router.post("/api-keys", response_model=ApiKeyCreateResponse)
def api_keys_create(payload: ApiKeyCreateRequest, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    api_key, plaintext = create_api_key(db, actor.account, payload.name)
    return ApiKeyCreateResponse(api_key=api_key, plaintext_key=plaintext)


@router.get("/api-keys", response_model=ApiKeyListResponse)
def api_keys_list(actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    return ApiKeyListResponse(api_keys=list_api_keys(db, actor.account))


@router.post("/api-keys/{api_key_id}/revoke", response_model=ApiKeyRevokeResponse)
def api_keys_revoke(api_key_id: int, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    api_key = revoke_api_key(db, actor.account, api_key_id)
    return ApiKeyRevokeResponse(api_key=api_key)


@router.post("/subjects", response_model=SubjectOut, status_code=status.HTTP_201_CREATED)
def subjects_create(payload: SubjectCreateRequest, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    return _subject_response(create_subject(db, actor.account, payload.display_name))


@router.get("/subjects", response_model=SubjectListResponse)
def subjects_list(actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    return SubjectListResponse(subjects=[_subject_response(subject) for subject in list_subjects(db, actor.account)])


@router.get("/subjects/{subject_id}", response_model=SubjectOut)
def subjects_get(subject_id: int, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    return _subject_response(get_subject(db, actor.account, subject_id))


@router.delete("/subjects/{subject_id}", response_model=SubjectOut)
def subjects_delete(subject_id: int, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    return _subject_response(delete_subject(db, actor.account, subject_id))


@router.post("/locations", response_model=LocationCreateResponse, status_code=status.HTTP_201_CREATED)
def locations_create(payload: LocationCreateRequest, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    return LocationCreateResponse(location=_location_response(create_location(db, actor.account, payload.code, payload.display_name)))


@router.get("/locations", response_model=LocationListResponse)
def locations_list(actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    return LocationListResponse(locations=[_location_response(location) for location in list_locations(db, actor.account)])


@router.post("/locations/{location_id}/image", response_model=LocationCreateResponse)
async def locations_image_upload(
    location_id: int,
    image: UploadFile = File(...),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    return LocationCreateResponse(location=_location_response(await store_location_image(db, actor.account, location_id, image)))


@router.get("/locations/{location_id}/image")
def locations_image_get(location_id: int, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    stored = get_location_image_file(db, actor.account, location_id)
    return FileResponse(stored.path, media_type=stored.mime_type)


@router.delete("/locations/{location_id}/image", response_model=LocationCreateResponse)
def locations_image_delete(location_id: int, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    return LocationCreateResponse(location=_location_response(remove_location_image(db, actor.account, location_id)))


@router.post("/episodes", response_model=EpisodeResponse, status_code=status.HTTP_201_CREATED)
def episodes_create(payload: EpisodeCreateRequest, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    episode = create_episode(db, actor.account, payload.subject_id, payload.location_id, payload.protocol_version, utc_now(), actor.actor_type, actor.actor_id)
    return _episode_response(episode)


@router.get("/episodes", response_model=EpisodeListResponse)
def episodes_list(subject_id: int | None = None, status: str | None = None, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    return EpisodeListResponse(episodes=list_episodes(db, actor.account, subject_id=subject_id, status_name=status))


@router.get("/episodes/due", response_model=DueListResponse)
def episodes_due(subject_id: int | None = None, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    catch_up_episode_phases(db, reason="due-read", account=actor.account, subject_id=subject_id)
    return DueListResponse(due=due_items(db, actor.account, subject_id))


@router.get("/adherence/calendar", response_model=AdherenceCalendarResponse)
def adherence_calendar(
    episode_id: int | None = None,
    subject_id: int | None = None,
    location_id: int | None = None,
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    persisted: bool = False,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    rows = list_adherence_rows(
        db,
        actor.account,
        from_date,
        to_date,
        episode_id=episode_id,
        subject_id=subject_id,
        location_id=location_id,
        persisted=persisted,
    )
    return AdherenceCalendarResponse(days=[_adherence_day_response(row) for row in rows])


@router.get("/adherence/summary", response_model=AdherenceSummaryResponse)
def adherence_summary(
    episode_id: int | None = None,
    subject_id: int | None = None,
    location_id: int | None = None,
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    persisted: bool = False,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    rows = list_adherence_rows(
        db,
        actor.account,
        from_date,
        to_date,
        episode_id=episode_id,
        subject_id=subject_id,
        location_id=location_id,
        persisted=persisted,
    )
    return _adherence_summary_response(from_date, to_date, rows)


@router.get("/adherence/missed", response_model=AdherenceCalendarResponse)
def adherence_missed(
    episode_id: int | None = None,
    subject_id: int | None = None,
    location_id: int | None = None,
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    persisted: bool = False,
    include_partial: bool = False,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    rows = list_adherence_rows(
        db,
        actor.account,
        from_date,
        to_date,
        episode_id=episode_id,
        subject_id=subject_id,
        location_id=location_id,
        persisted=persisted,
    )
    statuses = {"missed", "partial"} if include_partial else {"missed"}
    return AdherenceCalendarResponse(days=[_adherence_day_response(row) for row in rows if row.status in statuses])


@router.post("/adherence/rebuild", response_model=AdherenceRebuildResponse)
def adherence_rebuild(payload: AdherenceRebuildRequest, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    if payload.episode_id is not None:
        rows = persist_episode_adherence(
            db,
            actor.account,
            payload.episode_id,
            payload.from_date,
            payload.to_date,
            source=payload.source,
        )
        return AdherenceRebuildResponse(episodes_processed=1, rows_persisted=len(rows))

    if not payload.active_only:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="all-episode rebuild is not supported")

    rows = rebuild_active_episode_adherence(db, actor.account, payload.from_date, payload.to_date, source=payload.source)
    return AdherenceRebuildResponse(episodes_processed=len({row.episode_id for row in rows}), rows_persisted=len(rows))


@router.get("/episodes/{episode_id}", response_model=EpisodeResponse)
def episodes_get(episode_id: int, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    return _episode_response(get_episode(db, actor.account, episode_id))


@router.get("/episodes/{episode_id}/adherence", response_model=EpisodeAdherenceResponse)
def episode_adherence(
    episode_id: int,
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    persisted: bool = False,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    rows = list_adherence_rows(db, actor.account, from_date, to_date, episode_id=episode_id, persisted=persisted)
    summary = _adherence_summary_response(from_date, to_date, rows)
    return EpisodeAdherenceResponse(
        episode_id=episode_id,
        from_date=from_date,
        to_date=to_date,
        summary=AdherenceSummaryMetrics(**summary.model_dump()),
        days=[_adherence_day_response(row) for row in rows],
    )


@router.post("/episodes/{episode_id}/heal", response_model=EpisodeResponse)
def episodes_heal(episode_id: int, payload: HealEpisodeRequest | None = None, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    healed_at = (payload.healed_at if payload else None) or utc_now()
    episode = heal_episode(db, actor.account, episode_id, healed_at, actor.actor_type, actor.actor_id)
    return _episode_response(episode)


@router.post("/episodes/{episode_id}/relapse", response_model=EpisodeResponse)
def episodes_relapse(episode_id: int, payload: RelapseEpisodeRequest, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    reported_at = payload.reported_at or utc_now()
    episode = relapse_episode(db, actor.account, episode_id, reported_at, payload.reason, actor.actor_type, actor.actor_id)
    return _episode_response(episode)


@router.post("/episodes/{episode_id}/advance", response_model=EpisodeResponse)
def episodes_advance(episode_id: int, payload: AdvanceEpisodeRequest | None = None, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    episode = advance_episode(db, actor.account, episode_id, utc_now(), actor.actor_type, actor.actor_id)
    return _episode_response(episode)


@router.post("/applications", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
def applications_create(payload: ApplicationCreateRequest, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    applied_at = payload.applied_at or utc_now()
    application = log_application(
        db,
        actor.account,
        payload.episode_id,
        applied_at,
        payload.treatment_type,
        payload.treatment_name,
        payload.quantity_text,
        payload.notes,
        actor.actor_type,
        actor.actor_id,
    )
    return _application_response(application)


@router.patch("/applications/{application_id}", response_model=ApplicationResponse)
def applications_update(application_id: int, payload: ApplicationUpdateRequest, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    application = update_application(
        db,
        actor.account,
        application_id,
        applied_at=payload.applied_at,
        treatment_type=payload.treatment_type,
        treatment_name=payload.treatment_name,
        quantity_text=payload.quantity_text,
        notes=payload.notes,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
    )
    return _application_response(application)


@router.delete("/applications/{application_id}", response_model=ApplicationResponse)
def applications_delete(application_id: int, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    application = delete_application(db, actor.account, application_id, utc_now(), actor.actor_type, actor.actor_id)
    return _application_response(application)


@router.post("/applications/{application_id}/void", response_model=ApplicationResponse)
def applications_void(application_id: int, payload: ApplicationVoidRequest, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    application = void_application(db, actor.account, application_id, payload.voided_at or utc_now(), payload.reason, actor.actor_type, actor.actor_id)
    return _application_response(application)


@router.get("/episodes/{episode_id}/applications", response_model=ApplicationListResponse)
def applications_list(episode_id: int, include_voided: bool = False, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    return ApplicationListResponse(applications=[ApplicationOut.model_validate(app) for app in list_applications(db, actor.account, episode_id, include_voided)])


@router.get("/episodes/{episode_id}/events", response_model=EventListResponse)
def events_list(episode_id: int, event_type: str | None = None, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    return _event_list(list_events(db, actor.account, episode_id, event_type=event_type))


@router.get("/episodes/{episode_id}/timeline", response_model=TimelineResponse)
def episode_timeline(episode_id: int, actor: ActorContext = Depends(get_current_actor), db: Session = Depends(get_db)):
    events = list_events(db, actor.account, episode_id)
    return TimelineResponse(timeline=events)
