// SynapseQC 지식베이스 챗봇 위젯 (좌하단 토글)
// Supabase Edge Function kb-answer 를 호출해 문서·웹 문구 기반으로 답한다.
// 사용: <script src="chatbot/chatbot.js" defer></script>
(function initKbChatbot() {
  const ENDPOINT = "https://tljyiuhrlahrqcobshun.supabase.co/functions/v1/kb-answer";
  // 공개(anon) 키: 지식베이스는 공개 읽기 전용이라 브라우저에 노출돼도 된다
  const ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRsanlpdWhybGFocnFjb2JzaHVuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTE4MzY5MDQsImV4cCI6MjEwNzQxMjkwNH0.x5gnjYjynDB_MjrMpOMs0n2QXeq6yjxOwrlsiSdE4Us";
  const SUGGESTIONS = [
    "PoC 신청은 어떻게 하나요?",
    "PatchCore와 YOLO는 어떻게 다른가요?",
    "엣지가 클라우드보다 얼마나 저렴한가요?",
  ];

  const css = `
  .kbc-root{--kbc-bg:#181c24;--kbc-bg-2:#1c2028;--kbc-bg-3:#262a33;--kbc-line:#3a494b;--kbc-text:#dfe2ee;
    --kbc-muted:#b9cacb;--kbc-accent:#00f2fe;--kbc-accent-ink:#00373a;--kbc-accent-soft:#6ff6ff;--kbc-error:#ffb4ab;
    position:fixed;left:24px;bottom:24px;z-index:60;font-family:Inter,system-ui,sans-serif;color:var(--kbc-text)}
  .kbc-toggle{width:56px;height:56px;border-radius:50%;border:0;cursor:pointer;display:grid;place-items:center;
    background:var(--kbc-accent);color:var(--kbc-accent-ink);box-shadow:0 0 24px rgba(0,242,254,.35),0 8px 24px rgba(0,0,0,.4);
    transition:transform .15s ease}
  .kbc-toggle:hover{transform:scale(1.05)}
  .kbc-toggle:focus-visible,.kbc-send:focus-visible,.kbc-chip:focus-visible,.kbc-close:focus-visible{outline:2px solid var(--kbc-accent-soft);outline-offset:2px}
  .kbc-toggle .material-symbols-outlined{font-size:28px}
  .kbc-panel{position:absolute;left:0;bottom:72px;width:min(380px,calc(100vw - 32px));height:min(560px,calc(100vh - 120px));
    display:flex;flex-direction:column;background:var(--kbc-bg);border:1px solid var(--kbc-line);border-radius:14px;
    box-shadow:0 24px 64px rgba(0,0,0,.55);overflow:hidden;transform-origin:bottom left;
    transition:opacity .16s ease,transform .16s ease}
  .kbc-panel[hidden]{display:flex;opacity:0;transform:translateY(8px) scale(.98);pointer-events:none;visibility:hidden}
  .kbc-head{display:flex;align-items:center;gap:10px;padding:12px 14px;background:var(--kbc-bg-2);border-bottom:1px solid var(--kbc-line)}
  .kbc-dot{width:8px;height:8px;border-radius:50%;background:var(--kbc-accent);box-shadow:0 0 8px var(--kbc-accent)}
  .kbc-title{font-weight:600;font-size:14px;flex:1}
  .kbc-sub{display:block;font-family:"JetBrains Mono",monospace;font-size:10px;letter-spacing:.06em;color:var(--kbc-muted);font-weight:400}
  .kbc-close{border:0;background:transparent;color:var(--kbc-muted);cursor:pointer;border-radius:6px;padding:4px;display:grid;place-items:center}
  .kbc-close:hover{background:var(--kbc-bg-3);color:var(--kbc-text)}
  .kbc-log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:12px;scroll-behavior:smooth}
  .kbc-msg{max-width:88%;padding:10px 12px;border-radius:12px;font-size:13.5px;line-height:1.6;word-break:keep-all;overflow-wrap:anywhere}
  .kbc-msg.user{align-self:flex-end;background:var(--kbc-accent);color:var(--kbc-accent-ink);border-bottom-right-radius:4px;white-space:pre-wrap}
  .kbc-msg.bot{align-self:flex-start;background:var(--kbc-bg-3);border-bottom-left-radius:4px}
  .kbc-msg.bot p{margin:0 0 6px}.kbc-msg.bot p:last-child{margin:0}
  .kbc-msg.bot ul,.kbc-msg.bot ol{margin:4px 0 6px;padding-left:18px}
  .kbc-msg.bot ul{list-style:disc}.kbc-msg.bot ol,.kbc-sources ol{list-style:decimal}
  .kbc-msg.bot strong{color:var(--kbc-accent-soft);font-weight:600}
  .kbc-msg.error{color:var(--kbc-error)}
  .kbc-cite{font-family:"JetBrains Mono",monospace;font-size:10.5px;color:var(--kbc-accent);text-decoration:none;vertical-align:super;margin-left:1px}
  .kbc-cite:hover{text-decoration:underline}
  .kbc-sources{margin-top:8px;border-top:1px solid var(--kbc-line);padding-top:6px;font-size:12px;color:var(--kbc-muted)}
  .kbc-sources summary{cursor:pointer;font-family:"JetBrains Mono",monospace;font-size:10.5px;letter-spacing:.05em}
  .kbc-sources ol{margin:6px 0 0;padding-left:18px}
  .kbc-sources li{margin:2px 0}
  .kbc-sources a{color:var(--kbc-muted)}.kbc-sources a:hover{color:var(--kbc-text)}
  .kbc-typing{display:inline-flex;gap:4px;padding:4px 0}
  .kbc-typing span{width:6px;height:6px;border-radius:50%;background:var(--kbc-muted);animation:kbc-blink 1.2s infinite ease-in-out}
  .kbc-typing span:nth-child(2){animation-delay:.15s}.kbc-typing span:nth-child(3){animation-delay:.3s}
  @keyframes kbc-blink{0%,80%,100%{opacity:.25}40%{opacity:1}}
  .kbc-chips{display:flex;flex-wrap:wrap;gap:6px}
  .kbc-chip{border:1px solid var(--kbc-line);background:transparent;color:var(--kbc-text);border-radius:999px;padding:6px 10px;font-size:12px;cursor:pointer;font-family:inherit}
  .kbc-chip:hover{border-color:var(--kbc-accent);color:var(--kbc-accent-soft)}
  .kbc-form{display:flex;gap:8px;padding:10px;border-top:1px solid var(--kbc-line);background:var(--kbc-bg-2)}
  .kbc-input{flex:1;resize:none;border:1px solid var(--kbc-line);background:var(--kbc-bg);color:var(--kbc-text);border-radius:10px;
    padding:9px 11px;font:inherit;font-size:13.5px;line-height:1.4;max-height:96px;outline:none}
  .kbc-input:focus{border-color:var(--kbc-accent)}
  .kbc-send{width:40px;border:0;border-radius:10px;background:var(--kbc-accent);color:var(--kbc-accent-ink);cursor:pointer;display:grid;place-items:center}
  .kbc-send:disabled{opacity:.4;cursor:default}
  @media (max-width:480px){.kbc-root{left:16px;bottom:16px}}
  @media (prefers-reduced-motion:reduce){.kbc-panel,.kbc-toggle{transition:none}.kbc-typing span{animation:none}}
  `;

  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  const root = document.createElement("div");
  root.className = "kbc-root";
  root.innerHTML = `
    <div class="kbc-panel" id="kbc-panel" role="dialog" aria-label="SynapseQC 도우미" hidden>
      <div class="kbc-head">
        <span class="kbc-dot" aria-hidden="true"></span>
        <div class="kbc-title">SynapseQC 도우미<span class="kbc-sub">PRD · 기술 보고서 · 웹 문구 기반 답변</span></div>
        <button class="kbc-close" type="button" aria-label="챗봇 닫기"><span class="material-symbols-outlined">close</span></button>
      </div>
      <div class="kbc-log" aria-live="polite"></div>
      <form class="kbc-form">
        <textarea class="kbc-input" rows="1" maxlength="1000" placeholder="궁금한 점을 물어보세요" aria-label="질문 입력"></textarea>
        <button class="kbc-send" type="submit" aria-label="보내기"><span class="material-symbols-outlined">send</span></button>
      </form>
    </div>
    <button class="kbc-toggle" type="button" aria-controls="kbc-panel" aria-expanded="false" aria-label="챗봇 열기">
      <span class="material-symbols-outlined">chat</span>
    </button>`;
  document.body.appendChild(root);

  const panel = root.querySelector(".kbc-panel");
  const toggle = root.querySelector(".kbc-toggle");
  const toggleIcon = toggle.querySelector(".material-symbols-outlined");
  const log = root.querySelector(".kbc-log");
  const form = root.querySelector(".kbc-form");
  const input = root.querySelector(".kbc-input");
  const send = root.querySelector(".kbc-send");
  let busy = false;

  function setOpen(open) {
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", open ? "챗봇 닫기" : "챗봇 열기");
    toggleIcon.textContent = open ? "close" : "chat";
    if (open) input.focus();
  }
  toggle.addEventListener("click", () => setOpen(panel.hidden));
  root.querySelector(".kbc-close").addEventListener("click", () => { setOpen(false); toggle.focus(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden) { setOpen(false); toggle.focus(); }
  });

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // 답변용 최소 마크다운: **굵게**, 목록(*, -, 1.), 문단, [n] 인용 → 출처 링크
  function renderAnswer(text, sources) {
    const byN = new Map(sources.map((s) => [s.n, s]));
    const inline = (s) => escapeHtml(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\[(\d+)\]/g, (m, n) => {
        const src = byN.get(Number(n));
        if (!src || !src.url) return m;
        return `<a class="kbc-cite" href="${escapeHtml(src.url)}" target="_blank" rel="noopener" title="${escapeHtml(src.title)}">[${n}]</a>`;
      });
    const out = [];
    let list = null;
    for (const raw of text.split("\n")) {
      const line = raw.trim();
      const bullet = line.match(/^[*-]\s+(.*)$/);
      const ordered = line.match(/^\d+\.\s+(.*)$/);
      const item = bullet || ordered;
      const type = bullet ? "ul" : "ol";
      if (item) {
        if (list !== type) { if (list) out.push(`</${list}>`); out.push(`<${type}>`); list = type; }
        out.push(`<li>${inline(item[1])}</li>`);
        continue;
      }
      if (list) { out.push(`</${list}>`); list = null; }
      if (line) out.push(`<p>${inline(line)}</p>`);
    }
    if (list) out.push(`</${list}>`);
    return out.join("");
  }

  function renderSources(sources) {
    if (!sources.length) return "";
    const items = sources.map((s) => {
      const label = escapeHtml(s.heading_path ? `${s.title} › ${s.heading_path}` : s.title);
      return s.url ? `<li><a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${label}</a></li>` : `<li>${label}</li>`;
    }).join("");
    return `<details class="kbc-sources"><summary>참고 자료 ${sources.length}건</summary><ol>${items}</ol></details>`;
  }

  function addMessage(role, html) {
    const el = document.createElement("div");
    el.className = `kbc-msg ${role}`;
    el.innerHTML = html;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }

  function showWelcome() {
    const el = addMessage("bot", "<p>안녕하세요! SynapseQC 제품, PoC, 모델·전처리 전략에 대해 물어보세요.</p>");
    const chips = document.createElement("div");
    chips.className = "kbc-chips";
    for (const q of SUGGESTIONS) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "kbc-chip";
      b.textContent = q;
      b.addEventListener("click", () => { chips.remove(); ask(q); });
      chips.appendChild(b);
    }
    el.appendChild(chips);
  }

  async function ask(question) {
    if (busy || !question.trim()) return;
    busy = true;
    send.disabled = true;
    addMessage("user", escapeHtml(question));
    const pending = addMessage("bot", '<span class="kbc-typing" aria-label="답변 작성 중"><span></span><span></span><span></span></span>');
    try {
      const res = await fetch(ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${ANON_KEY}`, apikey: ANON_KEY },
        body: JSON.stringify({ question }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.error) throw new Error(data.error || `요청 실패 (${res.status})`);
      const sources = data.sources || [];
      pending.innerHTML = renderAnswer(data.answer || "", sources) + renderSources(sources);
    } catch (e) {
      pending.classList.add("error");
      pending.textContent = e.message || "답변을 가져오지 못했습니다. 잠시 후 다시 시도하세요.";
    } finally {
      busy = false;
      send.disabled = false;
      log.scrollTop = log.scrollHeight;
      input.focus();
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value;
    input.value = "";
    input.style.height = "";
    ask(q);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${input.scrollHeight}px`;
  });

  showWelcome();
})();
