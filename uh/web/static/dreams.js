// --------------------------------------------------------------------------- //
// Dreams - read what the daemon produced, then decide.
//
// Mounted from JS, not from index.html: the panel is created on first open, so a
// broken dreams route can never stop the chat from loading.
//
// This is a classic script and app.js is an ES module, so app.js's helpers are
// not on `window`: the two below are local copies on purpose. Reaching for them
// from here failed silently once (`el is not defined`, caught by mountDreams,
// and the button simply never appeared).
// --------------------------------------------------------------------------- //
function dreamsEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function dreamsApi(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

const DREAM_IDLE_STATES = ["idle-monitoring", "dreaming"];

function dreamsPanel() {
  let panel = document.getElementById("dreams-panel");
  if (panel) return panel;

  panel = dreamsEl("aside", "dreams-panel");
  panel.id = "dreams-panel";
  panel.innerHTML = `
    <header class="dreams-head">
      <span>Rêves</span>
      <button id="dreams-close" class="ghost" title="Fermer">×</button>
    </header>
    <div class="dreams-daemon">
      <span id="dreams-state">Idle monitoring: arrêté</span>
      <button id="dreams-toggle" class="ghost">Activer le Rêve Éveillé</button>
    </div>
    <div id="dreams-list" class="dreams-list"></div>
    <div id="dreams-detail" class="dreams-detail"></div>
  `;
  document.body.appendChild(panel);
  panel.querySelector("#dreams-close").addEventListener("click", () => panel.remove());
  panel.querySelector("#dreams-toggle").addEventListener("click", toggleDaemon);
  return panel;
}

async function loadDreams() {
  const panel = dreamsPanel();
  const list = panel.querySelector("#dreams-list");
  const state = panel.querySelector("#dreams-state");
  const toggle = panel.querySelector("#dreams-toggle");
  let data;
  try {
    data = await dreamsApi("/api/dreams");
  } catch (err) {
    list.textContent = "Impossible de lire les rêves : " + err.message;
    return;
  }

  const daemon = data.daemon || {};
  const running = DREAM_IDLE_STATES.includes(daemon.state);
  const label = {
    "idle-monitoring": "Idle monitoring: actif",
    dreaming: "Dreaming…",
    unsupported: "Indisponible sur ce système",
    stopped: "arrêté",
  }[daemon.state] || "arrêté";
  state.textContent = `${label}${
    daemon.threshold_minutes ? ` · ${daemon.threshold_minutes} min` : ""
  }`;
  // The button labels the action it will perform, in both directions: a toggle
  // that still says "Activer" once the daemon runs is a lie.
  toggle.textContent = running ? "Arrêter le Rêve Éveillé" : "Activer le Rêve Éveillé";
  toggle.dataset.running = running ? "1" : "0";
  panel.classList.toggle("is-dreaming", daemon.state === "dreaming");
  panel.classList.toggle("is-monitoring", daemon.state === "idle-monitoring");

  const dreams = data.dreams || [];
  list.innerHTML = "";
  if (!dreams.length) {
    list.innerHTML = '<p class="dreams-empty">Aucun rêve encore. Lancez une passe ou activez le daemon.</p>';
    return;
  }
  dreams.forEach((dream, index) => {
    const row = dreamsEl("div", "dream-row");
    if (index === 0) row.classList.add("is-latest");
    row.innerHTML = `
      <div class="dream-title">${dream.stamp} · ${dream.status} · ${dream.ideas.length} idée(s)</div>
      <div class="dream-summary">${dream.summary || ""}</div>
      <div class="dream-actions">
        <button class="danger" data-act="delete">Effacer</button>
        <button class="ghost" data-act="archive">Stocker</button>
        <button class="primary" data-act="fulfill">Exaucer</button>
      </div>`;
    row.querySelectorAll("button[data-act]").forEach((button) => {
      button.addEventListener("click", async () => {
        const act = button.dataset.act;
        try {
          if (act === "delete") {
            await dreamsApi(`/api/dreams/${dream.stamp}`, { method: "DELETE" });
          } else if (act === "fulfill") {
            // One POST: the plan is written once, and the panel says where.
            const answer = await dreamsApi(`/api/dreams/${dream.stamp}/fulfill`, {
              method: "POST",
            });
            panel.querySelector("#dreams-detail").textContent =
              `Plan écrit (rien n'est appliqué) : ${answer.plan}`;
          } else {
            await dreamsApi(`/api/dreams/${dream.stamp}/${act}`, { method: "POST" });
          }
          await loadDreams();
        } catch (err) {
          panel.querySelector("#dreams-detail").textContent = err.message;
        }
      });
    });
    list.appendChild(row);
  });
}

// A start spawns a process and a stop waits for a tick: both write their status a
// moment later. Looking once would leave the panel showing "arrêté" over a daemon
// that is already monitoring - a stale line reads as a broken button.
async function waitForDaemon(wantRunning, tries = 16) {
  for (let i = 0; i < tries; i++) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    const data = await dreamsApi("/api/dreams").catch(() => null);
    const running = DREAM_IDLE_STATES.includes(((data || {}).daemon || {}).state);
    if (running === wantRunning) return;
  }
}

async function toggleDaemon() {
  const panel = dreamsPanel();
  const button = panel.querySelector("#dreams-toggle");
  const detail = panel.querySelector("#dreams-detail");
  const running = button.dataset.running === "1";
  button.disabled = true;
  try {
    await dreamsApi("/api/dreams/daemon/" + (running ? "stop" : "start"), { method: "POST" });
    if (running) {
      // A stop is a request, not a kill: the daemon leaves after its current
      // tick, so the panel says so instead of pretending it is already gone.
      detail.textContent = "Arrêt demandé : le daemon quitte à son prochain tick (≤ 1 min).";
    } else {
      await waitForDaemon(true);
    }
  } catch (err) {
    detail.textContent = err.message;
  } finally {
    button.disabled = false;
  }
  // The label follows the daemon's own state, not the click: a start that
  // refuses (unsupported platform) must not read as started.
  await loadDreams();
}

function mountDreams() {
  try {
    if (document.getElementById("dreams-open")) return;
    const target = document.querySelector(".topbar, header, #topbar") || document.body;
    const button = dreamsEl("button", "ghost dreams-open", "Rêves");
    button.id = "dreams-open";
    button.addEventListener("click", () => {
      loadDreams();
      dreamsPanel().classList.add("open");
    });
    target.appendChild(button);

    dreamsApi("/api/dreams")
      .then((data) => {
        const idea = (data.dreams || []).find((dream) => (dream.ideas || []).length);
        if (!idea) return;
        const badge = dreamsEl("button", "ghost idea-badge", "💡 Idée du jour");
        badge.id = "idea-badge";
        badge.title = idea.ideas[0].title;
        badge.addEventListener("click", () => {
          loadDreams();
          dreamsPanel().classList.add("open");
        });
        target.appendChild(badge);
      })
      .catch(() => {});
  } catch (err) {
    console.warn("dreams: mount failed", err);
  }
}

document.addEventListener("DOMContentLoaded", mountDreams);
