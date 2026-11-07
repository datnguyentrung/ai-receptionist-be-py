import hmac
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from app.clients.java_attendance_client import JavaAttendanceClient, JavaCloudError
from app.core.config import settings
from app.schemas.face_check_in_scan import (
    FaceCheckInScanAccepted,
    InsightFaceWarmupResponse,
)
from app.services.face_check_in_orchestrator import FaceCheckInOrchestrator
from app.utils.insightface_utils import is_face_app_initialized

router = APIRouter(tags=["Face Check-in Scans"])
SCAN_IMAGE = File(..., description="Kiosk face capture")
REQUEST_ID = Form(..., alias="requestId")
DEVICE_ID = Form(..., alias="deviceId", min_length=1, max_length=128)
UPLOAD_TICKET = Form(
    ..., alias="uploadTicket", min_length=1, max_length=4096
)


def _java_client(request: Request) -> JavaAttendanceClient:
    client = getattr(request.app.state, "java_attendance_client", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Fog integration is not ready",
        )
    return client


@router.post(
    "/internal/insightface/warmup",
    response_model=InsightFaceWarmupResponse,
)
async def warmup_insightface(
    internal_secret: str | None = Header(
        default=None, alias="X-Fog-Internal-Secret"
    ),
) -> InsightFaceWarmupResponse:
    if settings.FOG_INTERNAL_SECRET and not hmac.compare_digest(
        internal_secret or "", settings.FOG_INTERNAL_SECRET
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Fog internal secret",
        )
    ready = is_face_app_initialized()
    if not ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="InsightFace model is not ready",
        )
    return InsightFaceWarmupResponse(
        ready=True,
        model=settings.INSIGHTFACE_MODEL_NAME,
    )


@router.post(
    "/face-check-in-scans",
    response_model=FaceCheckInScanAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_face_check_in_scan(
    request: Request,
    file: UploadFile = SCAN_IMAGE,
    request_id: UUID = REQUEST_ID,
    device_id: str = DEVICE_ID,
    upload_ticket: str = UPLOAD_TICKET,
) -> FaceCheckInScanAccepted:
    orchestrator = FaceCheckInOrchestrator(_java_client(request))
    try:
        await orchestrator.process(
            file=file,
            request_id=request_id,
            device_id=device_id.strip(),
            upload_ticket=upload_ticket,
        )
    except JavaCloudError as exc:
        if exc.operation == "validate-upload" and exc.status_code is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Upload ticket is invalid or expired",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Java Cloud is unavailable",
        ) from exc
    return FaceCheckInScanAccepted(requestId=str(request_id))
