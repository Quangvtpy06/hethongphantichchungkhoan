"""Company profile and multi-source news analysis for Vietnamese equities.

The implementation is intentionally lightweight, but follows the useful ideas
from Tumiqa/vn-annual-report-miner (MIT): balanced multi-source collection,
strict company relevance checks, title similarity deduplication and graceful
fallback when a source is unavailable.

Example::

    python -m src.company_news --ticker HPG
    python -m src.company_news --ticker FPT --as-of 2026-10-08

The public ``run(ticker, as_of)`` entry point always returns a dictionary and
writes ``outputs/company_news.json``. Expected operational errors are returned
through an ``error`` field instead of escaping to the caller.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Iterable
from urllib.parse import quote, urljoin, urlparse
from zoneinfo import ZoneInfo

import pandas as pd


VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
VNSTOCK_DOCS = "https://vnstocks.com/docs/vnstock/tra-cuu-thong-tin-tham-chieu-reference"
METHODOLOGY_REFERENCE = "https://github.com/Tumiqa/vn-annual-report-miner"
VIETSTOCK_BASE = "https://vietstock.vn"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
}

POSITIVE_KEYWORDS = {
    "tăng trưởng",
    "tăng mạnh",
    "kỷ lục",
    "vượt kế hoạch",
    "mở rộng",
    "khởi công",
    "hoàn thành",
    "lợi nhuận tăng",
    "doanh thu tăng",
    "đơn hàng mới",
    "cổ tức",
    "phục hồi",
    "bứt phá",
}
NEGATIVE_KEYWORDS = {
    "suy giảm",
    "giảm mạnh",
    "thua lỗ",
    "lỗ ròng",
    "nợ quá hạn",
    "xử phạt",
    "điều tra",
    "đình chỉ",
    "rủi ro",
    "khó khăn",
    "chậm tiến độ",
    "thu hồi",
    "phá sản",
}


class CompanyNewsError(RuntimeError):
    """An expected, user-readable failure in the company-news workflow."""


def _clean_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if text.lower() in {"", "n/a", "na", "none", "null", "nan", "-"}:
        return None
    return text or None


def _json_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, float) and not (float("-inf") < value < float("inf")):
        return None
    return value


def _atomic_json_dump(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    os.replace(temporary, path)


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        missing = pd.isna(value)
        if isinstance(missing, bool) and missing:
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    iso_match = re.match(r"^(\d{4}-\d{2}-\d{2})[T\s]", text)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1))
        except ValueError:
            return None
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    return None if pd.isna(parsed) else parsed.date()


def _summary(text: str | None, fallback: str, limit: int = 420) -> str:
    source = re.sub(r"\s+", " ", text or fallback).strip()
    if not source:
        return fallback
    sentences = re.split(r"(?<=[.!?])\s+", source)
    result = " ".join(sentences[:2]).strip()
    if len(result) > limit:
        result = result[: limit - 1].rsplit(" ", 1)[0] + "…"
    return result


def classify_sentiment(text: str) -> str:
    """Rule-based, auditable classification; it is not investment advice."""

    normalized = re.sub(r"\s+", " ", text.lower())
    positive = sum(keyword in normalized for keyword in POSITIVE_KEYWORDS)
    negative = sum(keyword in normalized for keyword in NEGATIVE_KEYWORDS)
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return "neutral"


def _normalized_title(title: str) -> str:
    return re.sub(r"[^\w\s]", "", title.lower(), flags=re.UNICODE).strip()


def deduplicate_news(items: list[dict[str, Any]], threshold: float = 0.82) -> list[dict[str, Any]]:
    """Remove duplicate URLs and near-duplicate headlines while preserving order."""

    result: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: list[str] = []
    for item in items:
        title = _clean_text(item.get("title"))
        if not title:
            continue
        url = str(item.get("url") or "").strip()
        canonical_url = url.split("#", 1)[0].rstrip("/")
        if canonical_url and canonical_url in seen_urls:
            continue
        normalized = _normalized_title(title)
        if any(
            SequenceMatcher(None, normalized, previous).ratio() >= threshold
            for previous in seen_titles
        ):
            continue
        if canonical_url:
            seen_urls.add(canonical_url)
        seen_titles.append(normalized)
        result.append(item)
    return result


@dataclass(frozen=True)
class Portal:
    name: str
    search_url: str
    base_url: str
    article_pattern: str


PORTALS = (
    Portal(
        "CafeF",
        "https://cafef.vn/tim-kiem.chn?keywords={query}&page=1",
        "https://cafef.vn",
        r"-\d+\.chn$",
    ),
    Portal(
        "Tin Nhanh Chứng Khoán",
        "https://tinnhanhchungkhoan.vn/tim-kiem/?q={query}&page=1",
        "https://tinnhanhchungkhoan.vn",
        r"-(?:post)?\d+\.html$",
    ),
    Portal(
        "VnEconomy",
        "https://vneconomy.vn/tim-kiem.html?Text={query}&SortBy=newest&page=1",
        "https://vneconomy.vn",
        r"\.htm(?:l)?$",
    ),
    Portal(
        "VnExpress",
        "https://timkiem.vnexpress.net/?q={query}&cate_code=kinh-doanh&page=1",
        "https://vnexpress.net",
        r"-\d+\.html$",
    ),
)


class CompanyNewsAnalyzer:
    """Collect company reference data, events and verified multi-source news."""

    def __init__(
        self,
        ticker: str,
        as_of: str | date | None = None,
        *,
        output_dir: str | Path = "outputs",
        max_news: int = 12,
        lookback_days: int = 365,
        reference_factory: Callable[[], Any] | None = None,
        http_get: Callable[..., Any] | None = None,
        enable_web_news: bool = True,
    ) -> None:
        self.ticker = self._validate_ticker(ticker)
        self.as_of = self._validate_date(as_of)
        self.output_dir = Path(output_dir)
        self.max_news = max(1, min(int(max_news), 50))
        self.lookback_days = max(1, int(lookback_days))
        self.reference_factory = reference_factory
        self._http_get = http_get
        self.enable_web_news = enable_web_news
        self.warnings: list[str] = []
        self._session: Any = None

    @staticmethod
    def _validate_ticker(ticker: str) -> str:
        normalized = str(ticker or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{3,12}", normalized):
            raise CompanyNewsError(
                f"Mã cổ phiếu '{ticker}' không hợp lệ; chỉ dùng 3-12 chữ cái/số."
            )
        return normalized

    @staticmethod
    def _validate_date(value: str | date | None) -> date:
        if value is None:
            return datetime.now(VIETNAM_TZ).date()
        parsed = _parse_date(value)
        if parsed is None or (isinstance(value, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
            raise CompanyNewsError("as_of phải theo định dạng YYYY-MM-DD.")
        return parsed

    @property
    def json_path(self) -> Path:
        return self.output_dir / "company_news.json"

    def _reference(self) -> Any:
        if self.reference_factory is not None:
            return self.reference_factory()
        try:
            from vnstock import Reference  # type: ignore

            return Reference()
        except ImportError as exc:
            raise CompanyNewsError(
                "Thiếu vnstock. Cài bằng: pip install --extra-index-url "
                "https://vnstocks.com/api/simple vnstock"
            ) from exc

    def _request(self, url: str, *, timeout: int = 10) -> Any:
        if self._http_get is not None:
            return self._http_get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        if self._session is None:
            try:
                import requests
            except ImportError as exc:
                raise CompanyNewsError("Thiếu requests. Cài bằng: pip install requests") from exc
            self._session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                max_retries=requests.adapters.Retry(
                    total=2,
                    backoff_factor=0.4,
                    status_forcelist=(429, 500, 502, 503, 504),
                    allowed_methods=("GET",),
                )
            )
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)
        response = self._session.get(
            url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True
        )
        response.raise_for_status()
        return response

    @staticmethod
    def _safe_frame(call: Callable[[], Any], label: str, warnings: list[str]) -> pd.DataFrame:
        try:
            value = call()
            return value if isinstance(value, pd.DataFrame) else pd.DataFrame(value or [])
        except Exception as exc:
            warnings.append(f"Không lấy được {label}: {exc}")
            return pd.DataFrame()

    def fetch_reference_data(self) -> dict[str, pd.DataFrame]:
        reference = self._reference()
        company = reference.company(self.ticker)
        equity = reference.equity()
        calls: dict[str, Callable[[], Any]] = {
            "info": company.info,
            "news": company.news,
            "events": company.events,
            "shareholders": company.shareholders,
            "officers": company.officers,
            "subsidiaries": company.subsidiaries,
            "ownership": company.ownership,
            "capital_history": company.capital_history,
            "equities": equity.list,
            "industries": equity.list_by_industry,
            "exchanges": equity.list_by_exchange,
        }
        frames: dict[str, pd.DataFrame] = {}
        for name, call in calls.items():
            frames[name] = self._safe_frame(call, name, self.warnings)
        return frames

    def build_company_profile(self, frames: dict[str, pd.DataFrame]) -> dict[str, Any]:
        info = frames["info"]
        equities = frames["equities"]
        industries = frames["industries"]
        exchanges = frames["exchanges"]

        info_row = info.iloc[0].to_dict() if not info.empty else {}
        listed = equities[equities.get("symbol", pd.Series(dtype=str)).astype(str).str.upper() == self.ticker]
        listed_row = listed.iloc[0].to_dict() if not listed.empty else {}
        exchange_rows = exchanges[
            exchanges.get("symbol", pd.Series(dtype=str)).astype(str).str.upper() == self.ticker
        ]
        exchange_row = exchange_rows.iloc[0].to_dict() if not exchange_rows.empty else {}
        industry_rows = industries[
            industries.get("symbol", pd.Series(dtype=str)).astype(str).str.upper() == self.ticker
        ].copy()

        if not info_row and not listed_row and not exchange_row:
            raise CompanyNewsError(
                f"Không tìm thấy hồ sơ cho mã {self.ticker}; mã có thể sai hoặc ngoài phạm vi hỗ trợ."
            )

        company_name = (
            _clean_text(exchange_row.get("organ_name"))
            or _clean_text(listed_row.get("organ_name"))
            or self.ticker
        )
        industry: dict[str, str | None] = {
            "level_1": None,
            "level_2": None,
            "level_3": None,
            "level_4": None,
        }
        if not industry_rows.empty and "icb_level" in industry_rows:
            for _, row in industry_rows.iterrows():
                try:
                    level = int(row.get("icb_level"))
                except (TypeError, ValueError):
                    continue
                if 1 <= level <= 4:
                    industry[f"level_{level}"] = _clean_text(row.get("icb_name"))

        business_model = _clean_text(info_row.get("business_model"))
        segments: list[dict[str, Any]] = []
        if business_model:
            raw_lines = re.split(r"(?:\r?\n|\s+-\s+)", business_model)
            for line in raw_lines:
                cleaned = re.sub(r"^[\s•\-]+", "", line).strip(" .;")
                if len(cleaned) >= 8 and cleaned not in {item["segment"] for item in segments}:
                    segments.append(
                        {
                            "segment": cleaned,
                            "revenue_share": None,
                            "unit": "decimal",
                            "as_of": None,
                        }
                    )
                if len(segments) >= 10:
                    break

        profile_fields = (
            "founded_date",
            "listing_date",
            "number_of_employees",
            "ceo_name",
            "ceo_position",
            "company_type",
            "address",
            "phone",
            "email",
            "website",
            "auditor",
            "business_code",
            "tax_id",
            "history",
            "as_of_date",
        )
        profile = {field: _json_value(info_row.get(field)) for field in profile_fields}
        for field in ("founded_date", "listing_date", "as_of_date"):
            parsed_profile_date = _parse_date(info_row.get(field))
            profile[field] = (
                parsed_profile_date.isoformat() if parsed_profile_date else None
            )
        capital_rows = frames["capital_history"].copy()
        capital_payload: dict[str, Any] = {
            "value": None,
            "unit": "VND",
            "as_of": None,
        }
        if not capital_rows.empty and "date" in capital_rows:
            capital_rows["_parsed_date"] = capital_rows["date"].map(_parse_date)
            eligible = capital_rows[
                capital_rows["_parsed_date"].map(
                    lambda item: item is not None and item <= self.as_of
                )
            ].sort_values("_parsed_date", ascending=False)
            if not eligible.empty:
                latest_capital = eligible.iloc[0]
                capital_payload = {
                    "value": _json_value(latest_capital.get("charter_capital")),
                    "unit": _clean_text(latest_capital.get("currency")) or "VND",
                    "as_of": latest_capital["_parsed_date"].isoformat(),
                }
        profile.update(
            {
                "name": company_name,
                "english_name": _clean_text(exchange_row.get("en_organ_name")),
                "exchange": _clean_text(info_row.get("exchange"))
                or _clean_text(exchange_row.get("exchange")),
                "industry": industry,
                "charter_capital": capital_payload,
                "business_model": business_model,
                "revenue_structure": segments or None,
                "revenue_structure_note": (
                    "Các mảng được suy ra từ mô tả hoạt động của vnstock; tỷ trọng doanh thu "
                    "để null khi không có dữ liệu phân khúc đã kiểm chứng."
                ),
            }
        )
        return profile

    @staticmethod
    def _records(frame: pd.DataFrame, limit: int = 20) -> list[dict[str, Any]] | None:
        if frame.empty:
            return None
        records: list[dict[str, Any]] = []
        for row in frame.head(limit).to_dict("records"):
            normalized: dict[str, Any] = {}
            for key, value in row.items():
                clean_value = _json_value(value)
                key_text = str(key)
                if "date" in key_text.lower():
                    parsed_record_date = _parse_date(value)
                    clean_value = (
                        parsed_record_date.isoformat() if parsed_record_date else None
                    )
                if (
                    clean_value is not None
                    and isinstance(clean_value, (int, float))
                    and ("percentage" in key_text or "percent" in key_text)
                    and abs(clean_value) > 1
                ):
                    clean_value = round(float(clean_value) / 100, 8)
                normalized[key_text] = clean_value
            records.append(normalized)
        return records or None

    def normalize_events(self, frame: pd.DataFrame) -> list[dict[str, Any]]:
        if frame.empty:
            return []
        result: list[dict[str, Any]] = []
        for row in frame.to_dict("records"):
            event_date = None
            for key in ("event_date", "date", "record_date", "exright_date", "publish_time"):
                event_date = _parse_date(row.get(key))
                if event_date:
                    break
            if event_date and event_date > self.as_of:
                continue
            title = next(
                (
                    _clean_text(row.get(key))
                    for key in ("title", "event_name", "event_type", "content", "description")
                    if _clean_text(row.get(key))
                ),
                "Sự kiện doanh nghiệp",
            )
            result.append(
                {
                    "date": event_date.isoformat() if event_date else None,
                    "title": title,
                    "summary": _summary(
                        _clean_text(row.get("description") or row.get("content")), title
                    ),
                    "source": "vnstock/KBS",
                    "url": _clean_text(row.get("url")),
                }
            )
        return result

    def normalize_vnstock_news(self, frame: pd.DataFrame) -> list[dict[str, Any]]:
        if frame.empty:
            return []
        items: list[dict[str, Any]] = []
        cutoff = self.as_of - timedelta(days=self.lookback_days)
        for row in frame.to_dict("records"):
            published = _parse_date(row.get("publish_time") or row.get("date"))
            if published and not (cutoff <= published <= self.as_of):
                continue
            title = _clean_text(row.get("title"))
            if not title:
                continue
            url = _clean_text(row.get("url"))
            if url and url.startswith("/"):
                url = urljoin(VIETSTOCK_BASE, url)
            description = _clean_text(row.get("head") or row.get("description"))
            combined = f"{title}. {description or ''}"
            items.append(
                {
                    "date": published.isoformat() if published else None,
                    "title": title,
                    "summary": _summary(description, title),
                    "sentiment": classify_sentiment(combined),
                    "source": "Vietstock via vnstock/KBS",
                    "url": url,
                }
            )
        return items

    def _collect_portal_links(self, portal: Portal, query_text: str) -> list[tuple[str, str]]:
        try:
            response = self._request(portal.search_url.format(query=quote(query_text)), timeout=8)
            html = response.text[:2_000_000]
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            links: list[tuple[str, str]] = []
            for anchor in soup.find_all("a", href=True):
                href = str(anchor["href"]).strip()
                full_url = urljoin(portal.base_url, href)
                parsed = urlparse(full_url)
                if parsed.scheme not in {"http", "https"}:
                    continue
                if urlparse(portal.base_url).netloc not in parsed.netloc:
                    continue
                if not re.search(portal.article_pattern, parsed.path, re.I):
                    continue
                pair = (full_url, portal.name)
                if pair not in links:
                    links.append(pair)
                if len(links) >= 6:
                    break
            return links
        except Exception as exc:
            self.warnings.append(f"Nguồn {portal.name} không khả dụng: {exc}")
            return []

    def _collect_official_links(self, website: str | None) -> list[tuple[str, str]]:
        if not website:
            return []
        try:
            response = self._request(website, timeout=8)
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(response.text[:2_000_000], "html.parser")
            base_host = urlparse(website).netloc.replace("www.", "")
            result: list[tuple[str, str]] = []
            news_tokens = ("tin-tuc", "news", "bai-viet", "su-kien", "quan-he-co-dong")
            for anchor in soup.find_all("a", href=True):
                full_url = urljoin(website, str(anchor["href"]).strip())
                parsed = urlparse(full_url)
                if base_host not in parsed.netloc.replace("www.", ""):
                    continue
                if not any(token in parsed.path.lower() for token in news_tokens):
                    continue
                pair = (full_url, "Website doanh nghiệp")
                if pair not in result:
                    result.append(pair)
                if len(result) >= 8:
                    break
            return result
        except Exception as exc:
            self.warnings.append(f"Website doanh nghiệp không khả dụng: {exc}")
            return []

    @staticmethod
    def _brand_terms(ticker: str, company_name: str) -> list[str]:
        cleaned = re.sub(
            r"(?i)\b(công ty|ctcp|cổ phần|tập đoàn|tnhh|trách nhiệm hữu hạn|joint stock company)\b",
            " ",
            company_name,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
        terms = [ticker]
        if len(cleaned) >= 4:
            terms.append(cleaned)
        words = [word for word in cleaned.split() if len(word) >= 3]
        if len(words) >= 2:
            terms.append(" ".join(words[-2:]))
        return list(dict.fromkeys(term.lower() for term in terms if term))

    def _extract_article(
        self, url: str, source: str, brand_terms: list[str]
    ) -> dict[str, Any] | None:
        try:
            response = self._request(url, timeout=10)
            html = response.text[:2_500_000]
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            for element in soup(["script", "style", "noscript", "iframe", "svg"]):
                element.decompose()
            title_element = soup.find("h1")
            title = _clean_text(title_element.get_text(" ", strip=True) if title_element else None)
            if not title:
                meta = soup.find("meta", property="og:title")
                title = _clean_text(meta.get("content") if meta else None)
            if not title:
                return None

            description = None
            for selector in ("meta[property='og:description']", "meta[name='description']"):
                meta = soup.select_one(selector)
                if meta and meta.get("content"):
                    description = _clean_text(meta.get("content"))
                    break

            body = None
            for selector in (
                "article",
                "[itemprop='articleBody']",
                ".fck_detail",
                ".detail-content",
                ".content-detail",
                ".article-content",
                ".post-content",
            ):
                candidate = soup.select_one(selector)
                if candidate and len(candidate.get_text(" ", strip=True)) >= 80:
                    body = candidate
                    break
            text = _clean_text(body.get_text(" ", strip=True) if body else description) or ""
            generic_official_titles = {
                "công bố thông tin",
                "văn bản pháp lý",
                "báo cáo thường niên",
                "cáo bạch",
                "phát triển bền vững",
                "báo cáo tài chính",
                "quan hệ cổ đông",
                "tin tức",
            }
            if source == "Website doanh nghiệp" and title.lower().strip() in generic_official_titles:
                return None
            if len(text) < 80:
                return None
            combined = f"{title} {description or ''} {text[:4000]}".lower()
            if source != "Website doanh nghiệp" and not any(term in combined for term in brand_terms):
                return None

            published = None
            time_tag = soup.find("time")
            if time_tag:
                published = _parse_date(time_tag.get("datetime") or time_tag.get_text(" ", strip=True))
            if published is None:
                for selector in (
                    "meta[property='article:published_time']",
                    "meta[name='pubdate']",
                    "meta[itemprop='datePublished']",
                ):
                    meta = soup.select_one(selector)
                    if meta:
                        published = _parse_date(meta.get("content"))
                        if published:
                            break
            if published and not (
                self.as_of - timedelta(days=self.lookback_days) <= published <= self.as_of
            ):
                return None

            summary = _summary(description or text, title)
            return {
                "date": published.isoformat() if published else None,
                "title": title,
                "summary": summary,
                "sentiment": classify_sentiment(f"{title}. {summary}"),
                "source": source,
                "url": response.url,
            }
        except Exception:
            return None

    def fetch_web_news(self, profile: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.enable_web_news:
            return []
        company_name = str(profile.get("name") or self.ticker)
        query_text = f"{self.ticker} {company_name}"
        candidates: list[tuple[str, str]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                executor.submit(self._collect_portal_links, portal, query_text)
                for portal in PORTALS
            ]
            futures.append(
                executor.submit(self._collect_official_links, profile.get("website"))
            )
            for future in concurrent.futures.as_completed(futures):
                candidates.extend(future.result())

        unique_candidates = list(dict.fromkeys(candidates))[:24]
        brand_terms = self._brand_terms(self.ticker, company_name)
        articles: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [
                executor.submit(self._extract_article, url, source, brand_terms)
                for url, source in unique_candidates
            ]
            for future in concurrent.futures.as_completed(futures):
                item = future.result()
                if item:
                    articles.append(item)
        return articles

    @staticmethod
    def _sort_news(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            items,
            key=lambda item: item.get("date") or "0000-00-00",
            reverse=True,
        )

    def _evidence_summary(
        self, news: list[dict[str, Any]], sentiment: str, limit: int = 5
    ) -> list[dict[str, Any]] | None:
        selected = [item for item in news if item.get("sentiment") == sentiment][:limit]
        if not selected:
            return None
        return [
            {
                "summary": item["summary"],
                "date": item.get("date"),
                "source": item.get("source"),
                "url": item.get("url"),
            }
            for item in selected
        ]

    def build_sources(
        self, profile: dict[str, Any], news: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = [
            {"name": "vnstock/KBS", "url": VNSTOCK_DOCS, "date": self.as_of.isoformat()}
        ]
        website = _clean_text(profile.get("website"))
        if website:
            sources.append(
                {"name": "Website doanh nghiệp", "url": website, "date": self.as_of.isoformat()}
            )
        seen = {source["url"] for source in sources}
        for item in news:
            url = item.get("url")
            if url and url not in seen:
                sources.append(
                    {
                        "name": item.get("source") or "Nguồn tin",
                        "url": url,
                        "date": item.get("date"),
                    }
                )
                seen.add(url)
        return sources

    def run(self) -> dict[str, Any]:
        frames = self.fetch_reference_data()
        profile = self.build_company_profile(frames)
        vnstock_news = self.normalize_vnstock_news(frames["news"])
        web_news = self.fetch_web_news(profile)
        news = self._sort_news(deduplicate_news(vnstock_news + web_news))[: self.max_news]
        events = self.normalize_events(frames["events"])

        payload: dict[str, Any] = {
            "ticker": self.ticker,
            "as_of": self.as_of.isoformat(),
            "company": profile,
            "governance": {
                "major_shareholders": self._records(frames["shareholders"]),
                "officers": self._records(frames["officers"]),
                "ownership": self._records(frames["ownership"]),
                "subsidiaries": self._records(frames["subsidiaries"]),
                "capital_history": self._records(frames["capital_history"]),
            },
            "recent_events": events or None,
            "news": news or None,
            "news_summary": {
                "article_count": len(news),
                "positive_count": sum(item["sentiment"] == "positive" for item in news),
                "neutral_count": sum(item["sentiment"] == "neutral" for item in news),
                "negative_count": sum(item["sentiment"] == "negative" for item in news),
                "method": "rule_based_keyword_classification",
            },
            "growth_drivers": self._evidence_summary(news, "positive"),
            "risks": self._evidence_summary(news, "negative"),
            "sources": self.build_sources(profile, news),
            "methodology_reference": {
                "name": "vn-annual-report-miner (MIT) - multi-source news methodology",
                "url": METHODOLOGY_REFERENCE,
            },
            "warnings": self.warnings,
        }
        _atomic_json_dump(payload, self.json_path)
        return payload


def run(
    ticker: str,
    as_of: str | date | None = None,
    *,
    output_dir: str | Path = "outputs",
) -> dict[str, Any]:
    """Integration entry point; return a dictionary and never crash callers."""

    normalized = str(ticker or "").strip().upper()
    path = Path(output_dir) / "company_news.json"
    try:
        return CompanyNewsAnalyzer(normalized, as_of, output_dir=output_dir).run()
    except Exception as exc:
        payload = {"ticker": normalized, "error": str(exc)}
        try:
            _atomic_json_dump(payload, path)
        except OSError:
            pass
        return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Thu thập hồ sơ doanh nghiệp, sự kiện và tin tức đa nguồn."
    )
    parser.add_argument("--ticker", required=True, help="Mã cổ phiếu, ví dụ HPG")
    parser.add_argument("--as-of", default=None, help="Ngày chốt YYYY-MM-DD")
    parser.add_argument("--output-dir", default="outputs", help="Thư mục đầu ra")
    parser.add_argument("--max-news", type=int, default=12, help="Số tin tối đa")
    parser.add_argument("--lookback-days", type=int, default=365, help="Số ngày tìm tin")
    parser.add_argument(
        "--no-web-news",
        action="store_true",
        help="Chỉ dùng vnstock, không quét các cổng tin/website doanh nghiệp",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> dict[str, Any]:
    args = _build_parser().parse_args(argv)
    normalized = str(args.ticker).strip().upper()
    try:
        result = CompanyNewsAnalyzer(
            normalized,
            args.as_of,
            output_dir=args.output_dir,
            max_news=args.max_news,
            lookback_days=args.lookback_days,
            enable_web_news=not args.no_web_news,
        ).run()
    except Exception as exc:
        result = {"ticker": normalized, "error": str(exc)}
        try:
            _atomic_json_dump(result, Path(args.output_dir) / "company_news.json")
        except OSError:
            pass
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return result


if __name__ == "__main__":
    main()
