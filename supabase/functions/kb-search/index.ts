// 지식베이스 검색: 질의 → Gemini 임베딩 → hybrid_search_kb (벡터 + 키워드)
//   POST { query: string, match_count?: number, source_types?: ("prd"|"report"|"web")[] }
// 지식베이스는 공개 읽기이므로 anon/publishable 키로 호출할 수 있다.
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";

const GEMINI_API_KEY = Deno.env.get("GEMINI_API_KEY");
const GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models";
const EMBED_MODEL = "gemini-embedding-2"; // kb_chunks.embedding 과 같은 모델/차원이어야 한다
const EMBED_DIM = 768;
const SOURCE_TYPES = ["prd", "report", "web"];

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

async function embedQuery(query: string): Promise<number[]> {
  const res = await fetch(`${GEMINI_BASE}/${EMBED_MODEL}:embedContent`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY! },
    body: JSON.stringify({
      content: { parts: [{ text: `task: search result | query: ${query}` }] },
      outputDimensionality: EMBED_DIM,
    }),
  });
  if (!res.ok) throw new Error(`Gemini embedContent ${res.status}: ${await res.text()}`);
  return (await res.json()).embedding.values;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "POST") return json({ error: "POST only" }, 405);
  if (!GEMINI_API_KEY) return json({ error: "GEMINI_API_KEY 시크릿이 설정되지 않았습니다." }, 500);

  let query: string, matchCount: number, sourceTypes: string[] | null;
  try {
    const body = await req.json();
    query = String(body.query ?? "").trim();
    matchCount = Math.min(Math.max(Number(body.match_count) || 8, 1), 20);
    sourceTypes = Array.isArray(body.source_types)
      ? body.source_types.filter((t: unknown) => SOURCE_TYPES.includes(String(t)))
      : null;
  } catch {
    return json({ error: "JSON 본문이 필요합니다." }, 400);
  }
  if (!query) return json({ error: "query 가 비어 있습니다." }, 400);
  if (query.length > 1000) return json({ error: "query 는 1000자 이내로 입력하세요." }, 400);

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_ANON_KEY")!,
    { global: { headers: { Authorization: req.headers.get("Authorization") ?? "" } } },
  );

  try {
    const embedding = await embedQuery(query);
    const { data, error } = await supabase.rpc("hybrid_search_kb", {
      query_text: query,
      query_embedding: JSON.stringify(embedding),
      match_count: matchCount,
      source_types: sourceTypes?.length ? sourceTypes : null,
    });
    if (error) throw error;
    return json({ results: data });
  } catch (e) {
    console.error(e);
    return json({ error: "검색 중 오류가 발생했습니다." }, 500);
  }
});
