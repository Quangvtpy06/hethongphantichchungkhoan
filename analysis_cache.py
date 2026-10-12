"""Cache JSON có thời hạn cho dữ liệu cơ bản và tin tức theo mã/ngày."""
import hashlib
import json
import os
import time
from uuid import uuid4
from pathlib import Path


def load_or_fetch(directory, kind, ticker, as_of, ttl, fetch, *, refresh=False):
    key = hashlib.sha256(f"v2:{kind}:{ticker}:{as_of}".encode()).hexdigest()
    path = Path(directory) / f"{key}.json"
    if not refresh:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            age = time.time() - entry["saved_at"]
            if 0 <= age < ttl and isinstance(entry["data"], dict):
                return entry["data"], True
        except (OSError, ValueError, KeyError, TypeError):
            pass
    data = fetch()
    # Không giữ lỗi hoặc kết quả thiếu dữ liệu do lỗi nguồn.
    if isinstance(data, dict) and data and not any(data.get(k) for k in ("error", "errors", "warnings")):
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.parent / f"{uuid4().hex}.tmp"
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump({"saved_at": time.time(), "data": data}, handle, ensure_ascii=False)
            os.replace(temporary, path)
        except (OSError, TypeError, ValueError):
            pass
        finally:
            if temporary and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
    return data, False
