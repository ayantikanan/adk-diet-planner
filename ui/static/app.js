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
    done: (a) => `Found ${a.meal_type || "meal"} options${a.vegetarian ? " (vegetarian)" : ""}` +
      (a.avoid_allergens?.length ? `, without ${a.avoid_allergens.join(", ")}` : ""),
  },
  build_week_menu: {
    tag: "Nutrition tool", kind: "mcp",
    running: () => "Building your 7-day menu",
    done: (a) => `Built your 7-day menu${a.avoid_allergens?.length ? `, without ${a.avoid_allergens.join(", ")}` : ""}`,
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
// `at` is an event timestamp in seconds (used when replaying a past plan).
const openSteps = new Map(); // call id or name -> { li, started, call }
const clock = (at) => (at ? at * 1000 : performance.now());

function addStep(call, at) {
  $("activity-empty").hidden = true;
  const info = describe(call.name);
  const li = document.createElement("li");
  li.className = "step";
  li.dataset.state = "running";
  li.innerHTML = `<div class="step-title"></div>
    <div class="step-meta"><span class="tag ${info.kind}">${info.tag}</span><span class="time">running</span></div>`;
  li.querySelector(".step-title").textContent = info.running(call.args || {});
  $("steps").appendChild(li);
  openSteps.set(call.id || call.name, { li, started: clock(at), call });
}

function finishStep(resp, at) {
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
  li.querySelector(".time").textContent = `${(Math.max(0, clock(at) - started) / 1000).toFixed(1)} s`;

  const parsed = parseToolResult(response);
  const output = parsed ? JSON.stringify(parsed, null, 2)
    : typeof response.result === "string" ? response.result : JSON.stringify(response, null, 2);
  const details = document.createElement("details");
  details.innerHTML = "<summary>Show output</summary><pre></pre>";
  details.querySelector("pre").textContent = output;
  li.appendChild(details);

  if (resp.name === "calculate_daily_calories") updateFacts(parsed);
  if (resp.name === "build_week_menu") renderWeek(parsed);
}

// One orchestrator event -> steps and answer text. Returns the answer text, if any.
function applyEvent(event, answer, at) {
  let text = null;
  for (const part of event.content?.parts || []) {
    if (part.functionCall) addStep(part.functionCall, at);
    if (part.functionResponse) finishStep(part.functionResponse, at);
    if (part.text && !part.thought && event.author !== "user") {
      text = part.text;
      answer.className = "answer";
      answer.innerHTML = renderMarkdown(text);
      addSwapButtons(answer);
    }
  }
  return text;
}

function addQuestion(text) {
  const q = document.createElement("p");
  q.className = "question";
  q.textContent = text;
  $("answers").appendChild(q);
}

function addAnswer(text) {
  const answer = document.createElement("article");
  answer.className = "answer thinking";
  answer.textContent = text;
  $("answers").appendChild(answer);
  return answer;
}

// ---------- Memory: one lasting user id per browser ----------
const USER_KEY = "dietPlannerUserId";
let userId = null;
try { userId = localStorage.getItem(USER_KEY); } catch { /* private mode: no memory */ }

function rememberUserId(id) {
  userId = id;
  try { localStorage.setItem(USER_KEY, id); } catch { /* ignore */ }
}

const FORM_VALUES = {
  activity: { sedentary: "sedentary", light: "lightly active", moderate: "moderately active", active: "very active" },
  goal: { lose: "lose weight", maintain: "maintain my weight", gain: "gain weight" },
};

function setRadio(form, name, value) {
  const input = form.querySelector(`input[name="${name}"][value="${value}"]`);
  if (input) input.checked = true;
}

async function loadMemory() {
  if (!userId) return;
  try {
    const memory = await (await fetch(`/api/users/${userId}/memory`)).json();
    const p = memory.profile;
    if (!p) return;
    const form = $("profile-form");
    form.elements.age.value = p.age;
    form.elements.weight.value = p.weight_kg;
    form.elements.height.value = p.height_cm;
    if (FORM_VALUES.activity[p.activity]) form.elements.activity.value = FORM_VALUES.activity[p.activity];
    setRadio(form, "sex", p.sex);
    setRadio(form, "goal", FORM_VALUES.goal[p.goal]);
    if (memory.vegetarian !== null && memory.vegetarian !== undefined) {
      setRadio(form, "diet", memory.vegetarian ? "vegetarian" : "non-vegetarian");
    }
    applyExtras(form, memory);
    $("memory-note").hidden = false;
  } catch { /* memory is optional */ }
}

async function refreshHistory() {
  if (!userId) return;
  try {
    const items = await (await fetch(`/api/users/${userId}/history`)).json();
    const list = $("history-list");
    list.innerHTML = "";
    for (const item of items) {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.session = item.session_id;
      button.innerHTML = '<span class="history-title"></span><span class="history-meta"><span></span><span></span></span>';
      button.querySelector(".history-title").textContent = item.title;
      const [when, kcal] = button.querySelectorAll(".history-meta span");
      when.textContent = item.updated
        ? new Date(item.updated * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
      kcal.textContent = item.kcal ? `${item.kcal.toLocaleString()} kcal` : "";
      button.addEventListener("click", () => openPast(item.session_id));
      li.appendChild(button);
      list.appendChild(li);
    }
    $("history").hidden = items.length === 0;
    markCurrent();
  } catch { /* history is optional */ }
}

function markCurrent() {
  for (const b of document.querySelectorAll("#history-list button")) {
    b.setAttribute("aria-current", String(Boolean(session && b.dataset.session === session.session_id)));
  }
}

// Shows a past conversation again, and lets the user carry on with it.
async function openPast(sessionId) {
  if (busy) return;
  resetView();
  try {
    const r = await fetch(`/api/users/${userId}/history/${sessionId}`);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Couldn't open that plan.");
    const { events } = await r.json();
    let answer = null;
    for (const event of events) {
      const parts = event.content?.parts || [];
      if (event.author === "user") {
        const text = parts.filter((p) => p.text).map((p) => p.text).join(" ");
        if (!text) continue;
        addQuestion(text);
        answer = addAnswer("No answer was saved for this message.");
      } else if (answer) {
        applyEvent(event, answer, event.timestamp);
      }
    }
    for (const { li } of openSteps.values()) li.dataset.state = "failed";
    openSteps.clear();
    session = { session_id: sessionId, user_id: userId };
    $("followup-form").hidden = false;
    $("plan-actions").hidden = false;
    $("new-plan").hidden = false;
    markCurrent();
    showTraceLink();
  } catch (err) {
    addAnswer(err.message).className = "answer error";
  }
}

async function forgetMe() {
  if (!userId || !window.confirm("Forget your saved details and delete all your past plans?")) return;
  try {
    await fetch(`/api/users/${userId}`, { method: "DELETE" });
  } catch { /* still forget locally */ }
  try { localStorage.removeItem(USER_KEY); } catch { /* ignore */ }
  userId = null;
  session = null;
  resetView();
  $("profile-form").reset();
  $("memory-note").hidden = true;
  $("history").hidden = true;
  $("history-list").innerHTML = "";
}

// ---------- Conversation ----------
async function ensureSession() {
  if (session) return session;
  const r = await fetch("/api/sessions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  if (!r.ok) {
    const { detail } = await r.json().catch(() => ({}));
    throw new Error(detail || "The planner isn't running. Start the orchestrator and try again.");
  }
  session = await r.json();
  rememberUserId(session.user_id);
  return session;
}

function setBusy(value) {
  busy = value;
  $("build-btn").disabled = value;
  $("build-btn").textContent = value ? "Building your plan\u2026" : "Build my plan";
  $("week-btn").disabled = value;
  for (const el of $("followup-form").elements) el.disabled = value;
  for (const b of document.querySelectorAll(".swap-btn")) b.disabled = value;
}

async function send(message) {
  if (busy) return;
  $("form-note").textContent = "";
  setBusy(true);
  addQuestion(message);
  const answer = addAnswer("Working on it\u2026 follow the steps under Agent activity.");

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
        finalText = applyEvent(event, answer) || finalText;
      }
    }
    if (!finalText) throw new Error("The planner finished without an answer. Try again in a moment.");
    $("followup-form").hidden = false;
    $("plan-actions").hidden = false;
    $("new-plan").hidden = false;
    showTraceLink();
    refreshHistory();
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
    `${f.get("activity")}, want to ${f.get("goal")}, ${f.get("diet")}.` + extrasText(f);
}

function resetView() {
  $("answers").innerHTML = "";
  $("steps").innerHTML = "";
  openSteps.clear();
  $("activity-empty").hidden = false;
  $("followup-form").hidden = true;
  $("new-plan").hidden = true;
  $("trace-link").hidden = true;
  $("cloud-trace-run").hidden = true;
  $("plan-actions").hidden = true;
  $("week").hidden = true;
  $("week-tabs").innerHTML = "";
  $("week-panels").innerHTML = "";
  for (const id of ["kcal", "protein", "carbs", "fat", "protein-pct", "carbs-pct", "fat-pct"]) $(id).textContent = "\u2014";
  $("facts-sub").textContent = "Fill in your details to build today's plan.";
}

$("profile-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  if (!form.checkValidity()) {
    $("form-note").textContent = "Check the highlighted fields: age, weight and height need realistic values.";
    form.reportValidity();
    return;
  }
  const week = e.submitter && e.submitter.id === "week-btn";
  send(profileMessage(form) + (week ? " Plan my whole week (7 days)." : ""));
});

$("followup-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("followup-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  send(text);
});

$("new-plan").addEventListener("click", () => {
  session = null;
  resetView();
  markCurrent();
});

$("forget-btn").addEventListener("click", forgetMe);

// ---------- Links to Grafana, Jaeger and Cloud Trace ----------
// In Cloud Shell Web Preview this page is https://8080-<host>.cloudshell.dev, and Grafana and
// Jaeger are https://3000-<host> and https://16686-<host>: rewrite the localhost links to match.
const PREVIEW = location.hostname.match(/^\d+-(.+\.cloudshell\.dev)$/);
function publicUrl(url) {
  if (!PREVIEW || !url) return url;
  return url.replace(/^http:\/\/(?:localhost|127\.0\.0\.1):(\d+)/, (_, port) => `https://${port}-${PREVIEW[1]}`);
}
fetch("/api/links").then((r) => r.json()).then((links) => {
  if (links.dashboard) $("dashboard-link").href = publicUrl(links.dashboard);
  if (links.traces) $("traces-link").href = publicUrl(links.traces);
  $("dashboard-link").hidden = !links.dashboard;  // Cloud Trace only mode: no Grafana or Jaeger
  $("traces-link").hidden = !links.traces;
  if (links.cloud_trace) {
    $("cloud-trace-link").href = links.cloud_trace;
    $("cloud-trace-link").hidden = false;
  }
}).catch(() => {});

async function showTraceLink() {
  try {
    const r = await fetch(`/api/trace/${session.session_id}`);
    const { url, trace_id, cloud_url } = await r.json();
    if (cloud_url) {
      $("cloud-trace-run").href = cloud_url;
      $("cloud-trace-run").hidden = false;
    }
    if (!url) return;
    $("trace-link").href = publicUrl(url);
    $("trace-link").textContent = trace_id ? "Open this run in Jaeger" : "Open recent runs in Jaeger";
    $("trace-link").hidden = false;
  } catch { /* tracing is optional */ }
}

// ---------- Allergies and dislikes ----------
function extrasText(f) {
  const allergies = f.getAll("allergy");
  const dislikes = String(f.get("dislikes") || "").trim();
  let text = allergies.length ? ` Allergies: ${allergies.join(", ")}.` : " No food allergies.";
  if (dislikes) text += ` I don't like: ${dislikes}.`;
  return text;
}

function applyExtras(form, memory) {
  for (const box of form.querySelectorAll('input[name="allergy"]')) {
    box.checked = (memory.allergies || []).includes(box.value);
  }
  if (memory.dislikes) form.elements.dislikes.value = memory.dislikes.join(", ");
}

// ---------- Week view (filled from the build_week_menu tool's exact numbers) ----------
const MEAL_LABELS = { breakfast: "Breakfast", lunch: "Lunch", dinner: "Dinner", snack: "Snack" };

function renderWeek(week) {
  if (!week || !Array.isArray(week.days)) return;
  const tabs = $("week-tabs");
  const panels = $("week-panels");
  tabs.innerHTML = "";
  panels.innerHTML = "";
  week.days.forEach((day, i) => {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "week-tab";
    tab.id = `week-tab-${i}`;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", `week-day-${i}`);
    tab.innerHTML = "<span></span><small></small>";
    tab.querySelector("span").textContent = day.day.slice(0, 3);
    tab.querySelector("small").textContent = `${Number(day.total_calories).toLocaleString()} kcal`;
    tab.addEventListener("click", () => selectDay(i));
    tab.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const next = (i + (e.key === "ArrowRight" ? 1 : week.days.length - 1)) % week.days.length;
      selectDay(next);
      $(`week-tab-${next}`).focus();
    });
    tabs.appendChild(tab);

    const panel = document.createElement("div");
    panel.className = "week-day";
    panel.id = `week-day-${i}`;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", tab.id);
    panel.innerHTML = '<h3 class="week-day-title"></h3><table><tbody></tbody>' +
      '<tfoot><tr><th scope="row">Total</th><td></td><td></td></tr></tfoot></table>';
    panel.querySelector(".week-day-title").textContent =
      day.day + (day.portion && day.portion !== 1 ? ` (portions x${day.portion})` : "");
    const body = panel.querySelector("tbody");
    for (const [meal, dish] of Object.entries(day.meals || {})) {
      const row = body.insertRow();
      const th = document.createElement("th");
      th.scope = "row";
      th.textContent = MEAL_LABELS[meal] || meal;
      row.appendChild(th);
      row.insertCell().textContent = dish.name;
      row.insertCell().textContent = `${dish.calories} kcal`;
    }
    panel.querySelector("tfoot td:last-child").textContent = `${Number(day.total_calories).toLocaleString()} kcal`;
    panels.appendChild(panel);
  });
  $("week").hidden = false;
  selectDay(0);
}

function selectDay(index) {
  document.querySelectorAll(".week-tab").forEach((tab, i) => {
    tab.setAttribute("aria-selected", String(i === index));
    tab.tabIndex = i === index ? 0 : -1;
  });
  document.querySelectorAll(".week-day").forEach((panel, i) => { panel.hidden = i !== index; });
}

// ---------- Swap a meal (adds a button to each row of a Meal | Dish table) ----------
function addSwapButtons(answer) {
  for (const table of answer.querySelectorAll("table")) {
    const head = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim().toLowerCase());
    if (head[0] !== "meal" || head[1] !== "dish") continue;
    for (const row of table.querySelectorAll("tbody tr")) {
      const meal = (row.cells[0]?.textContent || "").trim();
      const dish = (row.cells[1]?.textContent || "").trim();
      if (!/^(breakfast|lunch|dinner|snack)$/i.test(meal) || !dish) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "swap-btn";
      button.textContent = "Swap";
      button.setAttribute("aria-label", `Swap ${meal}: ${dish}`);
      button.disabled = busy;
      button.addEventListener("click", () =>
        send(`Swap my ${meal.toLowerCase()} (${dish}) for a different option with similar calories.`));
      row.cells[1].appendChild(button);
    }
  }
}

// ---------- Print or save as PDF ----------
$("print-btn").addEventListener("click", () => {
  const today = new Date().toLocaleDateString([], { dateStyle: "long" });
  $("print-title").textContent = `Daily Diet Planner - plan printed ${today}`;
  window.print();
});

// ---------- Start ----------
refreshHealth();
setInterval(refreshHealth, 10000);
loadMemory();
refreshHistory();
