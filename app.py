import logging
import os
import sqlite3
import time
from typing import Optional
from urllib.parse import urlparse

import feedparser
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

FEED_URL = "https://news.hada.io/rss/news"

MISSKEY_BASE = os.environ["MISSKEY_BASE"].rstrip("/")
MISSKEY_TOKEN = os.environ["MISSKEY_TOKEN"]

VISIBILITY = os.getenv("VISIBILITY", "public")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))
DB_PATH = os.getenv("DB_PATH", "/data/state.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
USER_AGENT = os.getenv("USER_AGENT", "GeekNewsMisskeyBot/1.0")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def validate_config() -> None:
    parsed = urlparse(MISSKEY_BASE)
    if parsed.scheme != "https":
        raise ValueError("대상 서버는 반드시 https를 사용해야 합니다.")
    if not parsed.netloc:
        raise ValueError("대상 서버가 올바르지 않습니다.")
    if not MISSKEY_TOKEN.strip():
        raise ValueError("서버 링크를 입력하지 않았습니다.")
    if CHECK_INTERVAL < 30:
        raise ValueError("CHECK_INTERVAL(확인 주기)는 최소 30초 이상이어야 합니다.")


def create_http_session() -> Session:
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retry)

    session = Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MISSKEY_TOKEN}",
        "User-Agent": USER_AGENT,
    })
    return session


HTTP = create_http_session()


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_entries (
                entry_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                link TEXT NOT NULL,
                published TEXT,
                seen_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_seen_at
            ON seen_entries(seen_at)
        """)
        conn.commit()


def count_seen() -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM seen_entries")
        return int(cur.fetchone()[0])


def has_seen(entry_id: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT 1 FROM seen_entries WHERE entry_id = ? LIMIT 1",
            (entry_id,)
        )
        return cur.fetchone() is not None


def mark_seen(entry_id: str, title: str, link: str, published: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO seen_entries
            (entry_id, title, link, published, seen_at)
            VALUES (?, ?, ?, ?, strftime('%s','now'))
        """, (entry_id, title, link, published))
        conn.commit()


def prune_seen(limit: int = 5000) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM seen_entries")
        total = int(cur.fetchone()[0])

        if total <= limit:
            return

        delete_count = total - limit
        conn.execute("""
            DELETE FROM seen_entries
            WHERE entry_id IN (
                SELECT entry_id
                FROM seen_entries
                ORDER BY seen_at ASC
                LIMIT ?
            )
        """, (delete_count,))
        conn.commit()

        logging.info("Pruned %s old entries", delete_count)


def normalize_entry_id(entry) -> Optional[str]:
    return (
        entry.get("id")
        or entry.get("guid")
        or entry.get("link")
        or entry.get("title")
    )


def clean_text(value: Optional[str]) -> str:
    return (value or "").replace("\r", " ").replace("\n", " ").strip()


def build_post_text(entry) -> str:
    title = clean_text(entry.get("title")) or "(제목 없음)"
    link = clean_text(entry.get("link"))

    text = f"{title}\n{link}"
    return text[:3000]


def build_note_payload(text: str) -> dict:
    return {
        "visibility": VISIBILITY,
        "visibleUserIds": [],
        "cw": None,
        "localOnly": False,
        "reactionAcceptance": None,
        "noExtractMentions": False,
        "noExtractHashtags": False,
        "noExtractEmojis": False,
        "replyId": None,
        "renoteId": None,
        "channelId": None,
        "text": text,
        "fileIds": [],
        "mediaIds": [],
        "poll": None,
    }


def post_to_misskey(text: str) -> dict:
    url = f"{MISSKEY_BASE}/api/notes/create"
    payload = build_note_payload(text)

    response = HTTP.post(
        url,
        json=payload,
        timeout=(5, 20),
    )
    response.raise_for_status()
    return response.json()


def fetch_feed_entries():
    logging.info("피드 가져오는 중: %s", FEED_URL)

    response = HTTP.get(
        FEED_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=(5, 20),
    )
    response.raise_for_status()

    feed = feedparser.parse(response.content)

    if getattr(feed, "bozo", 0):
        logging.warning(
            "피드 파싱 중 경고: %s",
            getattr(feed, "bozo_exception", None)
        )

    if not getattr(feed, "entries", None):
        raise RuntimeError("피드에 내용이 없음")

    return list(feed.entries)


def first_run_seed(entries) -> None:
    seeded = 0

    for entry in entries:
        entry_id = normalize_entry_id(entry)
        if not entry_id:
            continue

        title = clean_text(entry.get("title"))
        link = clean_text(entry.get("link"))
        published = clean_text(entry.get("published") or entry.get("updated"))

        mark_seen(entry_id, title, link, published)
        seeded += 1

    logging.info("최초 실행입니다. %s 개의 게시물은 게시하지 않고 건너뜁니다.", seeded)


def check_and_post() -> None:
    entries = fetch_feed_entries()

    if count_seen() == 0:
        first_run_seed(entries)
        return

    new_entries = []
    for entry in entries:
        entry_id = normalize_entry_id(entry)
        if not entry_id:
            continue

        if not has_seen(entry_id):
            new_entries.append((entry_id, entry))

    logging.info("%s 개의 새로운 게시물이 감지되었습니다.", len(new_entries))

    for entry_id, entry in reversed(new_entries):
        title = clean_text(entry.get("title"))
        link = clean_text(entry.get("link"))
        published = clean_text(entry.get("published") or entry.get("updated"))
        text = build_post_text(entry)

        try:
            post_to_misskey(text)
            mark_seen(entry_id, title, link, published)
            logging.info("게시: %s", title)
        except Exception:
            logging.exception("게시 중 오류 발생: entry_id=%s title=%s", entry_id, title)

    prune_seen(limit=5000)


def main() -> None:
    validate_config()
    init_db()

    while True:
        try:
            check_and_post()
        except Exception:
            logging.exception("루프 중 에러 발생")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()