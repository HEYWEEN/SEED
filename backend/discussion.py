from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_DISCUSSION_CHARS = 160_000


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(pattern=r"^M[1-9][0-9]*$", max_length=12)
    kind: Literal["body", "comment"]
    text: str = Field(min_length=1, max_length=MAX_DISCUSSION_CHARS)


class Discussion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=1000)
    messages: list[Message] = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def validate_discussion(self):
        if sum(len(message.text) for message in self.messages) > MAX_DISCUSSION_CHARS:
            raise ValueError("Discussion exceeds the input limit.")
        if any(not message.text.strip() for message in self.messages):
            raise ValueError("Message text is empty.")
        if len({message.message_id for message in self.messages}) != len(self.messages):
            raise ValueError("Message IDs must be unique.")
        return self


class CacheLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    full_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class FullSummary(Discussion):
    mode: Literal["full"]


class EvaluationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["evaluations"]
    analysis_id: str = Field(pattern=r"^[0-9a-f]{32}$")
