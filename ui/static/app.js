// Daily Diet Planner - browser logic.
// Talks only to ui/app.py, which relays the orchestrator's live events.

const $ = (id) => document.getElementById(id);
let session = null; // { session_id, user_id }
let busy = false;

// ---------- What each tool / agent step is called in the UI ----------
const STEPS = {
  calculate_daily_calories: {
    tag: "Nutrition tool", kind: "mcp",
    running: () => "Calculating your calorie and macro targets",
    done: () => "Calculated your calorie and macro targets",
  },
  get_meal_options: {
    tag: "Nutrition tool", kind: "mcp",
    running: (a) => `Looking up ${a.meal_type || "meal"} options`,
    done: (a) => `Found ${a.meal_type || "meal"} options${a.vegetarian ? " (vegetarian)" : ""}`,
  },
  grocery_agent: {
    tag: "Partner agent", kind: "a2a",
    running: () => "Grocery agent is writing your shopping list",
    done: () => "Grocery agent wrote your shopping list",
  },
  workout_agent: {
    tag: "Partner agent", kind: "a2a",
    running: () => "Workout agent is planning your exercise",
    done: () => "Workout agent planned your exercise",
  },
};
const describe = (name) => STEPS[name] || {
  tag: "Step", kind: "mcp", running: () => `Running ${name}`, done: () => `Finished ${name}`,
};

// ---------- Service health ----------
async function refreshHealth() {
  try {
    const health = await (await fetch("/api/health")).json();
    for (const li of document.querySelectorAll("#status li")) {
      li.dataset.up = String(Boolean(health[li.dataset.service]));
      li.title = health[li.dataset.service] ? "Running" : "Not reachable";
    }
  } catch {
    for (const li of document.querySelectorAll("#status li")) li.dataset.up = "false";
  }
}

// ---------- Small, safe Markdown renderer (escapes all HTML first) ----------
function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function inline(s) {
  return escapeHtml(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
}
function renderMarkdown(md) {
  const lines = md.replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (/^\s*\|/.test(line)) {                       // table
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      const body = rows.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r));
      const [head, ...rest] = body;
      out.push("<table><thead><tr>" + cells(head).map((c) => `<th>${inline(c)}</th>`).join("") +
        "</tr></thead><tbody>" + rest.map((r) => "<tr>" + cells(r).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") +
        "</tbody></table>");
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);       // heading
    if (h) { const n = Math.min(h[1].length + 1, 3); out.push(`<h${n}>${inline(h[2])}</h${n}>`); i++; continue; }
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {          // list (one level of nesting)
      const ordered = /^\s*\d+\./.test(line);
      const items = [];
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        const nested = /^\s{2,}/.test(lines[i]);
        const text = inline(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ""));
        if (nested && items.length) items[items.length - 1].children.push(text);
        else items.push({ text, children: [] });
        i++;
      }
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>` + items.map((it) => `<li>${it.text}` +
        (it.children.length ? "<ul>" + it.children.map((c) => `<li>${c}</li>`).join("") + "</ul>" : "") +
        "</li>").join("") + `</${tag}>`);
      continue;
    }
    const para = [];                                  // paragraph
    while (i < lines.length && lines[i].trim() && !/^\s*(\||#|([-*+]|\d+\.)\s)/.test(lines[i])) para.push(lines[i++]);
    if (para.length) out.push(`<p>${inline(para.join(" "))}</p>`);
    else i++;
  }
  return out.join("\n");
}

// ---------- Nutrition Facts label ----------
function parseToolResult(response) {
  const text = response?.content?.[0]?.text;
  if (text) { try { return JSON.parse(text); } catch { return null; } }
  return response?.structuredContent || null;
}
function updateFacts(targets) {
  if (!targets || !targets.daily_calories) return;
  const kcal = targets.daily_calories;
  const pct = (grams, perGram) => `${Math.round((grams * perGram / kcal) * 100)}%`;
  $("kcal").textContent = kcal.toLocaleString();
  $("protein").textContent = `${targets.protein_g} g`;
  $("carbs").textContent = `${targets.carbs_g} g`;
  $("fat").textContent = `${targets.fat_g} g`;
  $("protein-pct").textContent = pct(targets.protein_g, 4);
  $("carbs-pct").textContent = pct(targets.carbs_g, 4);
  $("fat-pct").textContent = pct(targets.fat_g, 9);
  $("facts-sub").textContent = "Your daily target, from the calorie tool.";
  const facts = $("facts");
  facts.classList.remove("is-updated");
  void facts.offsetWidth; // restart the one entrance animation
  facts.classList.add("is-updated");
}

// ---------- Activity steps ----------
const openSteps = new Map(); // call id or name -> { li, started }

function addStep(call) {
  $("activity-empty").hidden = true;
  const info = describe(call.name);
  const li = document.createElement("li");
  li.className = "step";
  li.dataset.state = "running";
  li.innerHTML = `<div class="step-title"></div>
    <div class="step-meta"><span class="tag ${info.kind}">${info.tag}</span><span class="time">running</span></div>`;
  li.querySelector(".step-title").textContent = info.running(call.args || {});
  $("steps").appendChild(li);
  openSteps.set(call.id || call.name, { li, started: performance.now(), call });
}

function finishStep(resp) {
  const key = openSteps.has(resp.id) ? resp.id : resp.name;
  const entry = openSteps.get(key);
  if (!entry) return;
  openSteps.delete(key);
  const { li, started, call } = entry;
  const info = describe(resp.name);
  const response = resp.response || {};
  const failed = Boolean(response.error || response.isError ||
    /failed to initialize|unavailable|not found/i.test(String(response.result || "")));
  li.dataset.state = failed ? "failed" : "done";
  li.querySelector(".step-title").textContent = failed
    ? `${info.done(call.args || {})} \u2014 failed` : info.done(call.args || {});
  li.querySelector(".time").textContent = `${((performance.now() - started) / 1000).toFixed(1)} s`;

  const parsed = parseToolResult(response);
  const output = parsed ? JSON.stringify(parsed, null, 2)
    : typeof response.result === "string" ? response.result : JSON.stringify(response, null, 2);
  const details = document.createElement("details");
  details.innerHTML = "<summary>Show output</summary><pre></pre>";
  details.querySelector("pre").textContent = output;
  li.appendChild(details);

  if (resp.name === "calculate_daily_calories") updateFacts(parsed);
}

// ---------- Conversation ----------
async function ensureSession() {
  if (session) return session;
  const r = await fetch("/api/sessions", { method: "POST" });
  if (!r.ok) {
    const { detail } = await r.json().catch(() => ({}));
    throw new Error(detail || "The planner isn't running. Start the orchestrator and try again.");
  }
  session = await r.json();
  return session;
}

function setBusy(value) {
  busy = value;
  $("build-btn").disabled = value;
  $("build-btn").textContent = value ? "Building your plan\u2026" : "Build my plan";
  for (const el of $("followup-form").elements) el.disabled = value;
}

async function send(message, { showQuestion }) {
  if (busy) return;
  $("form-note").textContent = "";
  setBusy(true);

  if (showQuestion) {
    const q = document.createElement("p");
    q.className = "question";
    q.textContent = message;
    $("answers").appendChild(q);
  }
  const answer = document.createElement("article");
  answer.className = "answer thinking";
  answer.textContent = "Working on it\u2026 follow the steps under Agent activity.";
  $("answers").appendChild(answer);

  let finalText = "";
  try {
    const s = await ensureSession();
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...s, message }),
    });
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop();
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        const event = JSON.parse(line.slice(5));
        if (event.error || event.errorMessage) throw new Error(event.error || event.errorMessage);
        for (const part of event.content?.parts || []) {
          if (part.functionCall) addStep(part.functionCall);
          if (part.functionResponse) finishStep(part.functionResponse);
          if (part.text && !part.thought && event.author !== "user") {
            finalText = part.text;
            answer.className = "answer";
            answer.innerHTML = renderMarkdown(finalText);
          }
        }
      }
    }
    if (!finalText) throw new Error("The planner finished without an answer. Try again in a moment.");
    $("followup-form").hidden = false;
    $("new-plan").hidden = false;
  } catch (err) {
    answer.className = "answer error";
    answer.textContent = /fetch|network/i.test(err.message)
      ? "Can't reach the web app server. Check that ui/app.py is running."
      : err.message;
    for (const { li } of openSteps.values()) li.dataset.state = "failed";
    openSteps.clear();
  } finally {
    setBusy(false);
    answer.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function profileMessage(form) {
  const f = new FormData(form);
  return `I'm ${f.get("age")}, ${f.get("sex")}, ${f.get("weight")} kg, ${f.get("height")} cm, ` +
    `${f.get("activity")}, want to ${f.get("goal")}, ${f.get("diet")}.`;
}

$("profile-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  if (!form.checkValidity()) {
    $("form-note").textContent = "Check the highlighted fields: age, weight and height need realistic values.";
    form.reportValidity();
    return;
  }
  send(profileMessage(form), { showQuestion: true });
});

$("followup-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("followup-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  send(text, { showQuestion: true });
});

$("new-plan").addEventListener("click", () => {
  session = null;
  $("answers").innerHTML = "";
  $("steps").innerHTML = "";
  $("activity-empty").hidden = false;
  $("followup-form").hidden = true;
  $("new-plan").hidden = true;
  for (const id of ["kcal", "protein", "carbs", "fat", "protein-pct", "carbs-pct", "fat-pct"]) $(id).textContent = "\u2014";
  $("facts-sub").textContent = "Fill in your details to build today's plan.";
});

refreshHealth();
setInterval(refreshHealth, 10000);
