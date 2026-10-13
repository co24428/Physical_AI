"""지식베이스 적재: build_chunks.build() 결과를 Edge Function kb-ingest 로 보내고 임베딩까지 채운다.

    pip install pypdf beautifulsoup4
    python rag/ingest.py

KB_INGEST_TOKEN 은 환경 변수나 rag/.env 에 둔다 (Supabase 의 KB_INGEST_TOKEN 시크릿과 같은 값).
내용 해시가 같은 문서는 건너뛰므로 여러 번 실행해도 된다.
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from build_chunks import build

FUNCTION_URL = "https://tljyiuhrlahrqcobshun.supabase.co/functions/v1/kb-ingest"
ENV_FILE = Path(__file__).resolve().parent / ".env"


def load_token():
    if os.environ.get("KB_INGEST_TOKEN"):
        return os.environ["KB_INGEST_TOKEN"]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "KB_INGEST_TOKEN":
                return value.strip().strip('"').strip("'")
    raise SystemExit("KB_INGEST_TOKEN 이 없습니다. 환경 변수나 rag/.env 에 설정하세요.")


def call(token, body):
    req = urllib.request.Request(
        FUNCTION_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-ingest-token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=150) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"kb-ingest {e.code}: {e.read().decode('utf-8', 'replace')}")


def main():
    token = load_token()
    for doc in build():
        result = call(token, {"action": "upsert", "documents": [doc]})["results"][doc["source_key"]]
        print(f"{result:9} {len(doc['chunks']):3} chunks  {doc['source_key']}")
    while True:
        res = call(token, {"action": "embed"})
        print(f"embedded {res['embedded']}, remaining {res['remaining']}")
        if res["embedded"] == 0 or res["remaining"] == 0:
            break


if __name__ == "__main__":
    main()
