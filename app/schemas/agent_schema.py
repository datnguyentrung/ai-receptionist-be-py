from typing import Literal

from pydantic import BaseModel, Field


class NoulQuestion(BaseModel):
    type: Literal["noul"] = "noul"
    instructions: str


class ChoiceQuestion(BaseModel):
    type: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str]


class ScoreQuestion(BaseModel):
    type: Literal["score"] = "score"
    instructions: str
    criteria: list[str] = Field(min_length=2, max_length=10)


class ClefRequest(BaseModel):
    model: Literal["clef", "clef-flash"] = "clef"
    state: str | dict[str, str]
    questions: dict[str, NoulQuestion | ChoiceQuestion | ScoreQuestion]


class NoulClefResponse(BaseModel):
    type: Literal["noul"] = "noul"
    noul: float = Field(..., description="The response from the AI model")


class ChoiceClefResponse(BaseModel):
    type: Literal["choice"] = "choice"
    choice: str = Field(..., description="The response from the AI model")
    probabilities: dict[str, float] = Field(
        ..., description="The probabilities from the AI model"
    )
    confidence: float = Field(..., description="The confidence from the AI model")


class ScoreClefResponse(BaseModel):
    type: Literal["score"] = "score"
    score: float = Field(..., description="The response from the AI model")
    legend: dict[str, str] = Field(..., description="The legend from the AI model")
    probabilities: dict[str, float] = Field(
        ..., description="The probabilities from the AI model"
    )
    confidence: float = Field(..., description="The confidence from the AI model")


class UsageClefResponse(BaseModel):
    input_tokens: int = Field(..., description="The number of prompt tokens")
    output_tokens: int = Field(..., description="The number of output tokens")


class ResultClefResponse(BaseModel):
    model: str = Field(..., description="The model used")
    answers: dict[str, NoulClefResponse | ChoiceClefResponse | ScoreClefResponse] = (
        Field(..., description="The answers")
    )
    usage: UsageClefResponse | None = Field(
        default=None, description="The token usage if provided"
    )


class RoutingAgentResponse(BaseModel):
    """Response schema for routing agent."""

    result: ResultClefResponse
    success: bool = Field(..., description="Whether the request was successful")
    errors: list[str] = Field(
        description="Any errors that occurred",
        default_factory=list,
    )
    messages: list[str] = Field(
        description="Any messages from the AI model",
        default_factory=list,
    )
