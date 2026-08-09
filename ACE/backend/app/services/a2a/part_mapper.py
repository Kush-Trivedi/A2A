from google.protobuf import json_format

from a2a.helpers import get_message_text
from a2a.types import Message, Part


class PartMapper:
    """Converts between A2A message parts and plain chat content."""

    def message_text(self, message: Message) -> str:
        return get_message_text(message) or ""

    def part_kind(self, part: Part) -> str:
        which = part.WhichOneof("content")
        return which or "unknown"

    def part_payload(self, part: Part) -> dict:
        kind = self.part_kind(part)
        payload: dict = {"kind": kind}
        if kind == "text":
            payload["text"] = part.text
        elif kind == "data":
            payload["data"] = json_format.MessageToDict(part.data)
        elif kind == "url":
            payload["url"] = part.url
            payload["media_type"] = part.media_type
            payload["filename"] = part.filename
        elif kind == "raw":
            payload["media_type"] = part.media_type
            payload["filename"] = part.filename
            payload["size_bytes"] = len(part.raw)
        return payload
