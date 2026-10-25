"""FastAPI app: thin endpoints over the service layer (RI-01), dependencies injected (RI-34)."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Literal

import numpy as np
import pyarrow as pa

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from viewer.config import Settings
from viewer.core import operators as ops
from datetime import datetime

from viewer import __version__
from viewer.core.kvt import Filters
from viewer.logging_config import bind, configure_logging, new_request_id
from viewer.service import export as expsvc
from viewer.service import retention
from viewer.api.static import mount_static
from viewer.service import measurements as uc
from viewer.service import operators as opsvc
from viewer.service import uploads as upsvc
from viewer.service.context import Services, build_services


def get_services(request: Request) -> Services:
    """The app's services; with Databricks, a view bound to the calling user's token (RI-40, RI-34)."""
    base: Services = request.app.state.services
    if base.user_factory is None:
        return base
    token = request.headers.get("x-forwarded-access-token") or base.settings.dbx_token
    if not token:
        raise HTTPException(401, "No user token: open the app through Databricks, or set VIEWER_DBX_TOKEN for a local run")
    return base.for_user(token, request.headers.get("x-forwarded-email") or request.headers.get("x-forwarded-preferred-username"))


class FileOut(BaseModel):
    id: str
    name: str
    size_bytes: int | None = None
    vin: str | None = None
    configuration_id: str | None = None
    start: str | None = Field(None, description="startOfSampling, local time, ISO 8601 (measurements from the metadata table)")
    end: str | None = None
    state: str = Field(description="not_ingested | queued | running | done | error")


class IngestStatusOut(BaseModel):
    id: str
    state: str
    phase: str = ""
    progress: float = 0.0
    message: str | None = None
    warnings: list[str] = []
    t0_us: int | None = Field(None, description="Absolute UTC µs of the measurement origin")
    duration_us: int | None = Field(None, description="Measurement length, µs after t0")
    timings: dict[str, float] = {}


class ChannelOut(BaseModel):
    name: str
    samples: int | None = None
    t_min_us: int | None = None
    t_max_us: int | None = None
    period_us: int | None = None
    native_hz: float | None = None
    assigned_hz: float | None = None
    n: int | None = None
    event: bool | None = None
    v_min: float | None = None
    v_max: float | None = None
    suggested_mode: str | None = None
    modes_allowed: list[str] | None = None


class ChannelsOut(BaseModel):
    complete: bool
    channels: list[ChannelOut]


Mode = Literal["step", "linear", "average"]
ARROW_MEDIA_TYPE = "application/vnd.apache.arrow.stream"


class ChannelRequest(BaseModel):
    name: str
    mode: Mode | None = Field(None, description="Default: step for event channels, linear otherwise")


class SeriesRequest(BaseModel):
    channels: list[ChannelRequest] = Field(min_length=1)
    j_start: int | None = Field(None, ge=0)
    j_end: int | None = Field(None, ge=0)


class SeriesOut(BaseModel):
    file: str
    channel: str
    n: int
    mode: Mode
    j_start: int
    values: list[float | None]


class SeriesResponse(BaseModel):
    step_base_us: int
    series: list[SeriesOut]


def _status_out(st) -> IngestStatusOut:
    return IngestStatusOut(
        id=st.id, state=st.state, phase=st.phase, progress=st.progress, message=st.message,
        warnings=st.warnings, t0_us=st.t0_us, duration_us=st.end_us, timings=st.timings,
    )


def _channel_out(c: dict) -> ChannelOut:
    out = ChannelOut(**c)
    if out.period_us:
        out.native_hz = 1e6 / out.period_us
    if out.n:
        out.assigned_hz = 10.0 / out.n
    if out.event is not None:
        out.modes_allowed = uc.modes_allowed({"event": out.event})
    return out


def _guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except opsvc.UnknownHandle as e:
        raise HTTPException(404, {"code": "unknown_handle", "handle": e.handle, "message": str(e)})
    except ops.OperatorError as e:
        raise HTTPException(422, str(e))
    except uc.NotFound as e:
        raise HTTPException(404, str(e))
    except uc.NotReady as e:
        raise HTTPException(409, str(e))
    except uc.BadRequest as e:
        raise HTTPException(422, str(e))


router = APIRouter(prefix="/api/v1")


@router.get("/files", response_model=list[FileOut])
def list_files(
    vin: str | None = None,
    configuration_id: str | None = None,
    start_from: datetime | None = Query(None, description="startOfSampling >= (local time)"),
    start_to: datetime | None = None,
    limit: int = Query(uc.LIST_LIMIT, ge=1, le=5000, description="At most this many measurements, most recent first"),
    svc: Services = Depends(get_services),
):
    """The measurement selector (RF-01); without filters the most recent measurements the user can see."""
    f = Filters(vin or None, configuration_id or None, start_from.replace(tzinfo=None) if start_from else None, start_to.replace(tzinfo=None) if start_to else None)
    return uc.list_files(svc, f if f.any() else None, limit)


@router.post("/files/{mid}/ingest", response_model=IngestStatusOut)
def ingest(mid: str, svc: Services = Depends(get_services)):
    return _status_out(_guard(uc.start_ingest, svc, mid))


@router.get("/files/{mid}/ingest", response_model=IngestStatusOut)
def ingest_status(mid: str, svc: Services = Depends(get_services)):
    return _status_out(_guard(uc.get_status, svc, mid))


@router.get("/files/{mid}/channels", response_model=ChannelsOut)
def channels(mid: str, svc: Services = Depends(get_services)):
    complete, items = _guard(uc.get_channels, svc, mid)
    return ChannelsOut(complete=complete, channels=[_channel_out(c) for c in items])


def _nan_to_none(a: np.ndarray) -> list:
    o = a.astype(object)
    o[np.isnan(a)] = None
    return o.tolist()


def encode_json(result: dict) -> Response:
    body = dict(
        step_base_us=result["step_base_us"],
        series=[{**{k: v for k, v in s.items() if k != "values"}, "values": _nan_to_none(s["values"])} for s in result["series"]],
    )
    return Response(json.dumps(body), media_type="application/json")


def encode_arrow(result: dict) -> Response:
    """One record batch per series, single float64 column 'values' (NaN <-> null); metadata in X-Series-Meta."""
    schema = pa.schema([("values", pa.float64())])
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, schema) as w:
        for s in result["series"]:
            arr = pa.array(s["values"], mask=np.isnan(s["values"]))
            w.write_batch(pa.record_batch([arr], schema=schema))
    meta = dict(
        step_base_us=result["step_base_us"],
        series=[{**{k: v for k, v in s.items() if k != "values"}, "length": int(s["values"].size)} for s in result["series"]],
    )
    return Response(
        sink.getvalue().to_pybytes(),
        media_type=ARROW_MEDIA_TYPE,
        headers={"X-Series-Meta": json.dumps(meta), "Access-Control-Expose-Headers": "X-Series-Meta"},
    )


@router.post(
    "/files/{mid}/series",
    response_model=SeriesResponse,
    responses={200: {"content": {ARROW_MEDIA_TYPE: {}}, "description": "JSON by columns, or Arrow IPC if Accept asks for it"}},
)
def series(mid: str, req: SeriesRequest, request: Request, svc: Services = Depends(get_services)):
    result = _guard(uc.get_series, svc, mid, [(c.name, c.mode) for c in req.channels], req.j_start, req.j_end)
    return encode_arrow(result) if ARROW_MEDIA_TYPE in request.headers.get("accept", "") else encode_json(result)


# ---- operators (RI-16 … RI-20) --------------------------------------------------------------------------


class InputSpecOut(BaseModel):
    name: str
    description: str = ""


class ParamSpecOut(BaseModel):
    name: str
    type: Literal["float", "int", "bool"]
    default: float | int | bool
    min: float | None = None
    max: float | None = None
    description: str = ""


class OperatorOut(BaseModel):
    name: str
    version: int
    description: str
    output: Literal["continuous", "discrete"]
    nature: Literal["pointwise", "temporal"]
    deprecated: bool
    promoted: bool
    inputs: list[InputSpecOut]
    params: list[ParamSpecOut]


class ChannelInput(BaseModel):
    file: str
    channel: str
    mode: Mode | None = None


class OperatorInput(BaseModel):
    """Exactly one of ch / handle / const."""

    ch: ChannelInput | None = None
    handle: str | None = None
    const: float | None = None


class OperatorRequest(BaseModel):
    inputs: list[OperatorInput] = Field(min_length=1)
    params: dict[str, float | int | bool] = {}
    shifts: dict[str, int] = Field({}, description="Shift in lattice steps per open measurement (RF-03)")
    detail: Literal["summary", "full"] = "summary"


class ResultStats(BaseModel):
    count: int
    nulls: int
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    last: float | None = None


class OperatorResult(BaseModel):
    handle: str
    operator: str
    n: int
    j_start: int
    length: int
    discrete: bool
    coincident: int = Field(description="Points where every channel input has a value; few or none means the shifts leave no common times")
    stats: ResultStats
    sparkline: list[list[float | None]]
    series: SeriesOut | None = None


@router.get("/operators", response_model=list[OperatorOut])
def operator_catalogue():
    return ops.describe()


@router.post(
    "/operators/{name}",
    response_model=OperatorResult,
    responses={404: {"description": "unknown_handle: an input handle is not in the cache"}, 200: {"content": {ARROW_MEDIA_TYPE: {}}}},
)
def apply_operator(name: str, req: OperatorRequest, request: Request, svc: Services = Depends(get_services)):
    inputs = [{k: v for k, v in i.model_dump(exclude_none=True).items()} for i in req.inputs]
    r = _guard(opsvc.apply, svc, name, inputs, req.params, req.shifts)
    summary = opsvc.summarise(r)
    head = dict(handle=r.handle, operator=r.operator, n=r.n, j_start=r.j_start, length=int(r.values.size), discrete=r.discrete, coincident=r.coincident, **summary)
    if req.detail == "summary":
        return Response(json.dumps({**head, "series": None}), media_type="application/json")
    full = opsvc.as_series_result(r)
    if ARROW_MEDIA_TYPE in request.headers.get("accept", ""):
        resp = encode_arrow(full)
        meta = json.loads(resp.headers["x-series-meta"])
        meta["result"] = head
        resp.headers["x-series-meta"] = json.dumps(meta)
        return resp
    body = json.loads(encode_json(full).body)
    return Response(json.dumps({**head, "series": body["series"][0]}), media_type="application/json")


# ---- request id and access log (RI-44) -------------------------------------------------------------------

access_log = logging.getLogger("viewer.access")
log = logging.getLogger("viewer.api")


class RequestLogMiddleware:
    """Pure ASGI (no response buffering): X-Request-ID in/out, one access record per request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
        rid = headers.get("x-request-id") or new_request_id()
        rid = "".join(c for c in rid if c.isalnum() or c in "-_")[:64] or new_request_id()
        user = headers.get("x-forwarded-email") or headers.get("x-forwarded-preferred-username") or "-"
        bind(rid, user)
        started = time.perf_counter()
        status = 500
        sent = 0

        async def send_wrapper(message):
            nonlocal status, sent
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            elif message["type"] == "http.response.body":
                sent += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            access_log.info(
                "request",
                extra=dict(method=scope["method"], path=scope["path"], route=getattr(route, "path", None), status=status,
                           ms=round((time.perf_counter() - started) * 1000, 1), bytes=sent),
            )


class VersionOut(BaseModel):
    version: str


class MeOut(BaseModel):
    user: str | None = None
    adapter: str
    version: str


@router.get("/version", response_model=VersionOut)
def version():
    return {"version": __version__}


@router.get("/me", response_model=MeOut)
def me(request: Request, svc: Services = Depends(get_services)):
    u = request.headers.get("x-forwarded-email") or request.headers.get("x-forwarded-preferred-username")
    return {"user": u, "adapter": svc.settings.adapter, "version": __version__}


# ---- uploads (RI-05, RF-37) ------------------------------------------------------------------------------


class UploadRequest(BaseModel):
    filename: str
    size: int = Field(gt=0)
    fingerprint: str = Field(description="Client-made identity of the file (name, size, modification time): repeating it resumes the upload")


class UploadOut(BaseModel):
    upload_id: str
    filename: str
    size: int
    chunk_size: int
    total_chunks: int
    received: list[int]


class ChunkOut(BaseModel):
    index: int
    received: int
    total_chunks: int


class UploadDone(BaseModel):
    id: str = Field(description="Measurement id (content hash); POST /files/{id}/ingest converts it")
    name: str
    size: int
    existing: bool = Field(description="The same content was already there: nothing was stored or needs converting again")


def _upload_guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except upsvc.UploadError as e:
        raise HTTPException(e.status, {"message": str(e), **e.extra} if e.extra else str(e))


@router.post("/uploads", response_model=UploadOut)
def upload_create(req: UploadRequest, svc: Services = Depends(get_services)):
    return _upload_guard(upsvc.create, svc, req.filename, req.size, req.fingerprint)


@router.get("/uploads/{uid}", response_model=UploadOut)
def upload_status(uid: str, svc: Services = Depends(get_services)):
    return _upload_guard(upsvc.status, svc, uid)


@router.put("/uploads/{uid}/chunks/{index}", response_model=ChunkOut)
async def upload_chunk(uid: str, index: int, request: Request, svc: Services = Depends(get_services)):
    data = await request.body()
    return _upload_guard(upsvc.put_chunk, svc, uid, index, data, request.headers.get("x-chunk-sha256"))


@router.post("/uploads/{uid}/complete", response_model=UploadDone)
def upload_complete(uid: str, svc: Services = Depends(get_services)):
    done = _upload_guard(upsvc.complete, svc, uid)
    log.info("upload complete", extra=dict(measurement=done["id"], file=done["name"], bytes=done["size"], existing=done["existing"]))
    retention.touch(svc, done["id"], force=True)
    for mid in retention.enforce(svc):
        log.info("evicted", extra=dict(measurement=mid, reason="storage limit"))
    return done


@router.delete("/uploads/{uid}", status_code=204)
def upload_cancel(uid: str, svc: Services = Depends(get_services)):
    upsvc.cancel(svc, uid)


# ---- export (RF-35) ----------------------------------------------------------------------------------------


class ExportSeries(BaseModel):
    label: str = Field(description="Value of the channel column, e.g. 'A: f10_000' or 'ƒ Smooth difference'")
    file: str | None = None
    channel: str = ""
    mode: Mode | None = None
    handle: str | None = Field(None, description="Result of an operator (calculated channel) instead of file/channel")


class CsvOptions(BaseModel):
    decimal: Literal[".", ","] = "."
    delimiter: Literal[",", ";", "\t"] = ","


class ExportRequest(BaseModel):
    format: Literal["csv", "parquet"]
    series: list[ExportSeries] = Field(min_length=1)
    k_from: int | None = Field(None, description="First lattice step of the window (inclusive); null = from the start")
    k_to: int | None = Field(None, description="Last lattice step (inclusive); null = to the end")
    shifts: dict[str, int] = {}
    csv: CsvOptions = CsvOptions()
    filename: str = "viewer-export"


def _clean_filename(name: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")[:80] or "viewer-export"


@router.post("/export", responses={200: {"content": {"text/csv": {}, "application/vnd.apache.parquet": {}}}})
def export_data(req: ExportRequest, svc: Services = Depends(get_services)):
    from fastapi.responses import FileResponse, StreamingResponse
    from starlette.background import BackgroundTask

    for s in req.series:
        if not s.handle and not s.file:
            raise HTTPException(422, f"Series {s.label!r} needs a file and channel, or a handle")
    series = [s.model_dump() for s in req.series]
    pieces = _guard(expsvc.collect, svc, series, req.k_from, req.k_to, req.shifts)
    log.info("export", extra=dict(format=req.format, series=len(series), rows=int(sum(p.k.size for p in pieces))))
    base = _clean_filename(req.filename)
    if req.format == "csv":
        if req.csv.decimal == req.csv.delimiter:
            raise HTTPException(422, "The decimal separator and the delimiter must differ")
        return StreamingResponse(
            expsvc.csv_chunks(pieces, req.csv.decimal, req.csv.delimiter), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{base}.csv"'},
        )
    path = expsvc.parquet_file(pieces)
    return FileResponse(
        path, media_type="application/vnd.apache.parquet", filename=f"{base}.parquet", background=BackgroundTask(os.unlink, path),
    )


# ---- storage and retention (RI-39) ---------------------------------------------------------------------------


class StorageOut(BaseModel):
    used_bytes: int
    limit_bytes: int = Field(description="0 = no limit")
    uploads_bytes: int
    parquet_bytes: int
    measurements: int


class PinRequest(BaseModel):
    ids: list[str] = Field(max_length=64)


@router.get("/storage", response_model=StorageOut)
def storage(svc: Services = Depends(get_services)):
    return retention.usage(svc)


@router.post("/sessions/pin", status_code=204)
def pin(req: PinRequest, svc: Services = Depends(get_services)):
    """Heartbeat of a browser session: the measurements it has open are in use and must not be evicted."""
    for mid in req.ids:
        if svc.catalog.get(mid) is not None:
            retention.touch(svc, mid, force=True)


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    app = FastAPI(title="Measurement viewer", version=__version__)
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level, settings.log_format)
    app.state.services = services or build_services(settings)
    app.add_middleware(RequestLogMiddleware)
    retention.enforce(app.state.services)  # at startup too
    app.include_router(router)
    mount_static(app)   # last: the catch-all must not shadow the API
    return app
