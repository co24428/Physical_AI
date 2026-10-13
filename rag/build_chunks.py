"""RAG 지식베이스용 청크 생성 스크립트.

레포의 문서(Markdown 보고서, PRD, PDF)와 웹 페이지(index.html, dashboard/*.html)의
문구를 섹션 단위로 나눈다. 단독 실행하면 확인용 rag/chunks.jsonl 을 만들고,
Supabase 적재는 rag/ingest.py 가 이 모듈의 build() 를 써서 한다.

    pip install pypdf beautifulsoup4
    python rag/build_chunks.py
"""

import hashlib
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "rag"
SITE_URL = "https://co24428.github.io/Physical_AI/"
REPO_URL = "https://github.com/co24428/Physical_AI/blob/main/"

MAX_CHARS = 1200  # 청크 최대 길이(문자)
MIN_CHARS = 150   # 이보다 짧은 섹션은 다음 섹션과 합친다

# (경로, source_type) — PRD PDF는 PRD.md와 내용이 같고 텍스트 추출이 깨져서 제외
MARKDOWN_SOURCES = [
    ("PRD/PRD.md", "prd"),
    ("report/model_selection_report.md", "report"),
    ("report/data_preprocessing.md", "report"),
    ("report/dataset_field_survey_checklist.md", "report"),
    ("report/factory_check_list.md", "report"),
]
# (경로, source_type, 제목) — PDF 는 첫 줄에서 제목을 안정적으로 뽑을 수 없어 직접 지정
PDF_SOURCES = [
    ("report/비전QC_인프라_아키텍처_의사결정.pdf", "report", "비전QC 인프라 아키텍처 의사결정: 엣지(Edge) vs 클라우드(Cloud)"),
]
WEB_SOURCES = [
    "index.html",
    "dashboard/overview.html",
    "dashboard/dataset-studio.html",
    "dashboard/automl-pipeline.html",
    "dashboard/edge-monitor.html",
]


def split_long(text, limit=MAX_CHARS):
    """문단 경계(빈 줄) 기준으로 limit 이하 조각으로 나눈다. 표는 쪼개지 않는다."""
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for para in re.split(r"\n\s*\n", text):
        if buf and len(buf) + len(para) + 2 > limit:
            parts.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        parts.append(buf)
    # 문단 하나가 limit 을 넘으면 줄 단위로 한 번 더 나눈다
    out = []
    for p in parts:
        if len(p) <= limit * 1.5:
            out.append(p)
            continue
        line_buf = ""
        for line in p.split("\n"):
            if line_buf and len(line_buf) + len(line) + 1 > limit:
                out.append(line_buf)
                line_buf = line
            else:
                line_buf = f"{line_buf}\n{line}" if line_buf else line
        if line_buf:
            out.append(line_buf)
    return out


def merge_short(sections):
    """[(heading_path, text)] 에서 너무 짧은 섹션을 다음 섹션 앞에 붙인다."""
    merged, carry = [], None
    for heading, text in sections:
        if carry:
            text = f"{carry[1]}\n\n{text}"
            heading = carry[0] or heading  # 합친 청크는 앞 섹션 제목부터 시작한다
            carry = None
        if len(text) < MIN_CHARS:
            carry = (heading, text)
            continue
        merged.append((heading, text))
    if carry:
        if merged:
            h, t = merged[-1]
            merged[-1] = (h, f"{t}\n\n{carry[1]}")
        else:
            merged.append(carry)
    return merged


def chunk_markdown(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    title = re.search(r"^# (.+)$", text, re.M).group(1).strip()
    sections, stack, buf = [], [], []

    def flush():
        body = "\n".join(buf).strip()
        body = re.sub(r"^\s*---\s*$", "", body, flags=re.M).strip()
        if body:
            sections.append((" > ".join(h for _, h in stack), body))
        buf.clear()

    for line in text.splitlines():
        m = re.match(r"^(#{1,4}) (.+)$", line)
        if m:
            flush()
            level = len(m.group(1))
            if level == 1:
                stack = []
                continue
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2).strip()))
            buf.append(line)
        else:
            buf.append(line)
    flush()
    return title, text, merge_short(sections)


def chunk_pdf(path, title):
    reader = PdfReader(ROOT / path)
    pages = [(p.extract_text() or "").strip() for p in reader.pages]
    sections = []
    for i, page in enumerate(pages, 1):
        first = page.splitlines()[0].strip() if page else ""
        if not page or first == "Image Sources":
            continue
        sections.append((f"p.{i} {first}", page))
    return title, "\n".join(pages), merge_short(sections)


def html_text(node):
    for icon in node.select(".material-symbols-outlined"):
        icon.decompose()
    text = node.get_text("\n", strip=True)
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    return "\n".join(l for l in lines if l)


def chunk_html(path):
    soup = BeautifulSoup((ROOT / path).read_text(encoding="utf-8"), "html.parser")
    for tag in soup(["script", "style", "svg", "noscript"]):
        tag.decompose()
    title = soup.title.get_text(strip=True)
    sections = []
    for sec in soup.find_all("section"):
        if sec.find_parent("section"):
            continue
        h = sec.find(["h1", "h2", "h3"])
        heading = h.get_text(" ", strip=True) if h else ""
        body = html_text(sec)
        if body:
            sections.append((heading, body))
    # 대시보드처럼 <section> 없이 div 로만 짜인 페이지는 main 을 제목(h1~h3) 경계로 나눈다
    main = soup.find("main") or soup.body
    if sum(len(b) for _, b in sections) < len(html_text(main)) / 2:
        sections = []
        marker = "␞HEADING␞"
        for h in main.find_all(["h1", "h2", "h3"]):
            h.insert_before(marker)
        for i, part in enumerate(html_text(main).split(marker)):
            part = part.strip()
            if part:
                # 첫 조각은 제목 앞의 내용이므로 페이지 제목을 쓴다
                sections.append((part.splitlines()[0] if i else title, part))
    # 회사 정보(연락처 등)는 랜딩 페이지 footer 에만 있다
    if path == "index.html" and soup.footer:
        sections.append(("회사 정보 및 연락처", html_text(soup.footer)))
    return title, soup.get_text(" ", strip=True), merge_short(sections)


def build():
    docs = []

    def add(path, source_type, url, title, raw, sections):
        chunks = []
        for heading, body in sections:
            for piece in split_long(body):
                chunks.append({"chunk_index": len(chunks), "heading_path": heading, "content": piece})
        docs.append({
            "source_key": path,
            "source_type": source_type,
            "title": title,
            "url": url,
            "content_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "metadata": {"path": path},
            "chunks": chunks,
        })

    for path, st in MARKDOWN_SOURCES:
        add(path, st, REPO_URL + path, *chunk_markdown(path))
    for path, st, title in PDF_SOURCES:
        add(path, st, REPO_URL + path, *chunk_pdf(path, title))
    for path in WEB_SOURCES:
        add(path, "web", SITE_URL + ("" if path == "index.html" else path), *chunk_html(path))
    return docs


def main():
    docs = build()
    with open(OUT_DIR / "chunks.jsonl", "w", encoding="utf-8") as f:
        for d in docs:
            for c in d["chunks"]:
                f.write(json.dumps({"source_key": d["source_key"], "title": d["title"], **c}, ensure_ascii=False) + "\n")
    total = sum(len(d["chunks"]) for d in docs)
    for d in docs:
        sizes = [len(c["content"]) for c in d["chunks"]]
        print(f"{d['source_type']:7} {len(sizes):3} chunks  max {max(sizes):5}  {d['source_key']}")
    print(f"total {len(docs)} docs, {total} chunks")


if __name__ == "__main__":
    main()
