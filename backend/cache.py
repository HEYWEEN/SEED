import hashlib
import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import time

_RESULT_ID = re.compile(r"[0-9a-f]{32}")
_CACHE_FORMAT = 1


def fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def discussion_fingerprint(discussion):
    # The extension hashes this same ordered array, including message order and boundaries.
    messages = [[row["message_id"], row["kind"], row["text"]] for row in discussion["messages"]]
    return fingerprint([discussion["title"], messages])


def result_identity(thread, kind, *inputs):
    return fingerprint([_CACHE_FORMAT, thread, kind, *inputs])[:32]


def write_local_text(path, contents):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".seed-write-", delete=False
    ) as temporary:
        pending = Path(temporary.name)
        try:
            temporary.write(contents)
            temporary.close()
            os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)


class ResultCache:
    def __init__(self, directory):
        self.directory = directory

    def read(self, result_id, kind, thread):
        if not _RESULT_ID.fullmatch(result_id):
            return None
        try:
            record = json.loads((self.directory / f"{result_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (
            not isinstance(record, dict)
            or record.get("format") != _CACHE_FORMAT
            or record.get("kind") != kind
            or record.get("thread") != thread
            or not isinstance(record.get("result"), dict)
            or not isinstance(record.get("generated_at"), (float, int))
        ):
            return None
        return {
            "id": result_id,
            "data": record["result"],
            "generated_at": record["generated_at"],
            "cached": True,
        }

    def save(self, result_id, kind, thread, result):
        record = {
            "format": _CACHE_FORMAT,
            "kind": kind,
            "thread": thread,
            "generated_at": time(),
            "result": result,
        }
        write_local_text(
            self.directory / f"{result_id}.json",
            json.dumps(record, ensure_ascii=False, separators=(",", ":")),
        )
