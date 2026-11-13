// --------------------------------------------------------------------------- //
// Dreams - read what the daemon produced, then decide.
//
// Mounted from JS, not from index.html: the panel is created on first open, so a
// broken dreams route can never stop the chat from loading.
// --------------------------------------------------------------------------- //
function dreamsPanel() {
  let panel = document.getElementById("dreams-panel");
  if (panel) return panel;

  panel = el("aside", "dreams-panel");
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
  return panel;
}

async function loadDreams() {
  const panel = dreamsPanel();
  const list = panel.querySelector("#dreams-list");
  const state = panel.querySelector("#dreams-state");
  let data;
  try {
    data = await api("/api/dreams");
  } catch (err) {
    list.textContent = "Impossible de lire les rêves : " + err.message;
    return;
  }

  const daemon = data.daemon || {};
  const label = {
    "idle-monitoring": "Idle monitoring: actif",
    dreaming: "Dreaming…",
    unsupported: "Indisponible sur ce système",
    stopped: "arrêté",
  }[daemon.state] || "arrêté";
  state.textContent = `${label}${
    daemon.threshold_minutes ? ` · ${daemon.threshold_minutes} min` : ""
  }`;
  panel.classList.toggle("is-dreaming", daemon.state === "dreaming");

  const dreams = data.dreams || [];
  list.innerHTML = "";
  if (!dreams.length) {
    list.innerHTML = '<p class="dreams-empty">Aucun rêve encore. Lancez une passe ou activez le daemon.</p>';
    return;
  }
  dreams.forEach((dream) => {
    const row = el("div", "dream-row");
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
          if (act === "delete") await api(`/api/dreams/${dream.stamp}`, { method: "DELETE" });
          else await api(`/api/dreams/${dream.stamp}/${act}`, { method: "POST" });
          if (act === "fulfill") {
            const answer = await api(`/api/dreams/${dream.stamp}/fulfill`, { method: "POST" });
            panel.querySelector("#dreams-detail").textContent =
              `Plan écrit (rien n'est appliqué) : ${answer.plan}`;
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

async function toggleDaemon() {
  const panel = dreamsPanel();
  const button = panel.querySelector("#dreams-toggle");
  const data = await api("/api/dreams").catch(() => ({ daemon: { state: "stopped" } }));
  const running = ["idle-monitoring", "dreaming"].includes((data.daemon || {}).state);
  await api("/api/dreams/daemon/" + (running ? "stop" : "start"), { method: "POST" });
  button.textContent = running ? "Activer le Rêve Éveillé" : "Arrêter le Rêve Éveillé";
  await loadDreams();
}

function mountDreams() {
  try {
    if (document.getElementById("dreams-open")) return;
    const target = document.querySelector(".topbar, header, #topbar") || document.body;
    const button = el("button", "ghost dreams-open", "Rêves");
    button.id = "dreams-open";
    button.addEventListener("click", () => {
      loadDreams();
      dreamsPanel().classList.add("open");
    });
    target.appendChild(button);

    api("/api/dreams")
      .then((data) => {
        const idea = (data.dreams || []).find((dream) => (dream.ideas || []).length);
        if (!idea) return;
        const badge = el("button", "ghost idea-badge", "💡 Idée du jour");
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
