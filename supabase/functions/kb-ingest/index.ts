// 지식베이스 적재: rag/ingest.py 가 보낸 문서를 kb_documents/kb_chunks 에 반영하고 임베딩을 채운다.
//   POST { action: "upsert", documents: [...] }  → 해시가 바뀐 문서만 청크 교체
//   POST { action: "embed" }                     → embedding 이 비어 있는 청크를 최대 BATCH 개 임베딩
// 인증: x-ingest-token 헤더 == KB_INGEST_TOKEN 시크릿 (service role 로 쓰기 때문에 JWT 대신 전용 토큰)
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";

const GEMINI_API_KEY = Deno.env.get("GEMINI_API_KEY");
const INGEST_TOKEN = Deno.env.get("KB_INGEST_TOKEN");
const GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models";
const EMBED_MODEL = "gemini-embedding-2"; // kb_chunks.embedding 과 같은 모델/차원이어야 한다
const EMBED_DIM = 768;
const BATCH = 50;

type Chunk = { chunk_index: number; heading_path: string; content: string };
type Doc = {
  source_key: string;
  source_type: "prd" | "report" | "web";
  title: string;
  url: string | null;
  content_hash: string;
  metadata?: Record<string, unknown>;
  chunks: Chunk[];
};

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function tokenMatches(given: string | null) {
  if (!INGEST_TOKEN || !given) return false;
  const a = new TextEncoder().encode(given);
  const b = new TextEncoder().encode(INGEST_TOKEN);
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

async function upsertDocument(doc: Doc) {
  const { data: existing, error: selErr } = await supabase
    .from("kb_documents").select("id, content_hash").eq("source_key", doc.source_key).maybeSingle();
  if (selErr) throw selErr;
  if (existing && existing.content_hash === doc.content_hash) return "unchanged";

  const { data: saved, error: upErr } = await supabase
    .from("kb_documents")
    .upsert({
      source_key: doc.source_key,
      source_type: doc.source_type,
      title: doc.title,
      url: doc.url,
      content_hash: doc.content_hash,
      metadata: doc.metadata ?? {},
      updated_at: new Date().toISOString(),
    }, { onConflict: "source_key" })
    .select("id").single();
  if (upErr) throw upErr;

  // 이 문서의 이전 청크만 교체한다
  const { error: delErr } = await supabase.from("kb_chunks").delete().eq("document_id", saved.id);
  if (delErr) throw delErr;
  const { error: insErr } = await supabase.from("kb_chunks").insert(
    doc.chunks.map((c) => ({
      document_id: saved.id,
      chunk_index: c.chunk_index,
      heading_path: c.heading_path ?? "",
      content: c.content,
    })),
  );
  if (insErr) throw insErr;
  return existing ? "updated" : "inserted";
}

async function embedPending() {
  const { data: rows, error } = await supabase
    .from("kb_chunks")
    .select("id, heading_path, content, kb_documents(title)")
    .is("embedding", null)
    .order("id")
    .limit(BATCH);
  if (error) throw error;
  if (!rows.length) return { embedded: 0, remaining: 0 };

  // gemini-embedding-2 는 taskType 대신 접두어로 문서/질의를 구분한다
  const requests = rows.map((r) => {
    const title = (r.kb_documents as unknown as { title: string } | null)?.title ?? "none";
    const text = r.heading_path ? `${r.heading_path}\n${r.content}` : r.content;
    return {
      model: `models/${EMBED_MODEL}`,
      content: { parts: [{ text: `title: ${title} | text: ${text}` }] },
      outputDimensionality: EMBED_DIM,
    };
  });
  const res = await fetch(`${GEMINI_BASE}/${EMBED_MODEL}:batchEmbedContents`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY! },
    body: JSON.stringify({ requests }),
  });
  if (!res.ok) throw new Error(`Gemini batchEmbedContents ${res.status}: ${await res.text()}`);
  const { embeddings } = await res.json();

  await Promise.all(rows.map(async (r, i) => {
    const { error: updErr } = await supabase
      .from("kb_chunks")
      .update({ embedding: JSON.stringify(embeddings[i].values) })
      .eq("id", r.id);
    if (updErr) throw updErr;
  }));

  const { count } = await supabase
    .from("kb_chunks").select("id", { count: "exact", head: true }).is("embedding", null);
  return { embedded: rows.length, remaining: count ?? 0 };
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return json({ error: "POST only" }, 405);
  if (!tokenMatches(req.headers.get("x-ingest-token"))) return json({ error: "unauthorized" }, 401);
  if (!GEMINI_API_KEY) return json({ error: "GEMINI_API_KEY 시크릿이 설정되지 않았습니다." }, 500);

  let body: { action?: string; documents?: Doc[] };
  try {
    body = await req.json();
  } catch {
    return json({ error: "JSON 본문이 필요합니다." }, 400);
  }

  try {
    if (body.action === "upsert") {
      const results: Record<string, string> = {};
      for (const doc of body.documents ?? []) results[doc.source_key] = await upsertDocument(doc);
      return json({ results });
    }
    if (body.action === "embed") return json(await embedPending());
    return json({ error: "action 은 upsert 또는 embed 여야 합니다." }, 400);
  } catch (e) {
    console.error(e);
    return json({ error: String(e instanceof Error ? e.message : e) }, 500);
  }
});
