from pydantic import BaseModel


class FaceCheckInScanAccepted(BaseModel):
    accepted: bool = True
    requestId: str


class InsightFaceWarmupResponse(BaseModel):
    ready: bool
    model: str
