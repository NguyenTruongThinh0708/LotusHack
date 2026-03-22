from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class UserCoords(BaseModel):
    lat: float
    lng: float
    accuracy: Optional[float] = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    user_coords: Optional[UserCoords] = None


class ChatResponse(BaseModel):
    reply: str
    intent: Dict[str, Any]
    shops: List[Dict[str, Any]]
    logs: List[str]


class STTResponse(BaseModel):
    text: str
    engine: str
    logs: List[str]


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class ErrorResponse(BaseModel):
    detail: str
    logs: Optional[List[str]] = None
