// Le panneau Machine (G19), sorti de `app.js` (P2-11).
//
// Frontière : ce module ne connaît que ce qu'on lui passe — `apiResult` et `$`.
// Il ne lit pas l'état global de l'application, ne décide de rien et ne mesure
// rien : il affiche ce que `/api/machine/scan` a répondu et n'envoie un
// téléchargement que sur un clic (le corps porte `accept: true`).

export function createMachinePanel({ apiResult, $ }) {
  // --- Machine (G19) -------------------------------------------------------- #
  // Le profil et la recommandation viennent d'UHB par `/api/machine/scan` : cette
  // vue n'invente aucun chiffre. Un modèle ne descend que par un clic sur son
  // bouton de consentement, qui affiche la taille ; une alternative qui ne tient
  // pas en VRAM demande une case cochée à la main, jamais pré-cochée.
  let machineScan = null;

  function machineGo(value) {
    return value === null || value === undefined ? "non mesuré" : `${value} Go`;
  }

  function machineLine(text, className) {
    const line = document.createElement("div");
    if (className) line.className = className;
    line.textContent = text;
    return line;
  }

  function machineBadge(text, className) {
    const badge = document.createElement("span");
    badge.className = className || "badge";
    badge.textContent = text;
    return badge;
  }

  function machineEntryCard(entry, options = {}) {
    const card = document.createElement("div");
    card.className = options.alternative ? "machine-entry machine-entry-alt" : "machine-entry";
    card.dataset.entryId = entry.id;

    const title = document.createElement("div");
    title.className = "machine-entry-title";
    const name = document.createElement("strong");
    name.textContent = entry.id;
    title.appendChild(name);
    if (entry.verified === false) title.appendChild(machineBadge("à vérifier", "badge badge-warn"));
    if (entry.mode === "offload") title.appendChild(machineBadge("experts en RAM", "badge"));
    card.appendChild(title);

    card.appendChild(
      machineLine(
        `${entry.file_gb} Go sur disque · ${machineGo(entry.needs && entry.needs.vram_gb)} de VRAM ` +
          `requis · licence ${entry.license || "à vérifier"}`,
        "machine-entry-meta",
      ),
    );
    if (entry.warning) card.appendChild(machineLine(entry.warning, "machine-warning"));

    if (options.alternative) {
      const consent = document.createElement("label");
      consent.className = "machine-consent";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = false; // jamais coché d'avance : le warning veut un geste
      box.dataset.machineConsent = entry.id;
      consent.appendChild(box);
      consent.appendChild(document.createTextNode(" je comprends l'avertissement"));
      card.appendChild(consent);

      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost";
      button.dataset.machineDownload = entry.id;
      button.textContent = `Télécharger (${entry.file_gb} Go)`;
      button.disabled = true; // activé par la case, pas par un défaut
      box.addEventListener("change", () => {
        button.disabled = !box.checked;
      });
      button.addEventListener("click", () => machineDownload(entry, button));
      card.appendChild(button);
    } else {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "primary";
      button.dataset.machineDownload = entry.id;
      button.textContent = `Télécharger (${entry.file_gb} Go)`;
      button.addEventListener("click", () => machineDownload(entry, button));
      card.appendChild(button);
    }
    return card;
  }

  function renderMachinePanel(payload) {
    const host = $("machine-panel");
    if (!host) return;
    host.textContent = "";
    const profile = payload.profile || {};

    const head = document.createElement("div");
    head.className = "machine-head";
    head.appendChild(machineLine("Cette machine", "machine-title"));
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "ghost";
    refresh.id = "machine-refresh";
    refresh.textContent = "Rescanner";
    refresh.addEventListener("click", () => loadMachinePanel());
    head.appendChild(refresh);
    host.appendChild(head);

    const gpu = profile.gpu;
    const ram = profile.ram || {};
    const rows = [
      ["Système", [profile.os, profile.arch, profile.python ? `Python ${profile.python}` : ""].filter(Boolean).join(" · ")],
      ["Processeur", profile.cpu ? `${profile.cpu.cores} cœurs${profile.cpu.model ? " · " + profile.cpu.model : ""}` : "non mesuré"],
      ["Mémoire vive", ram.total_gb ? `${machineGo(ram.free_gb)} libres sur ${ram.total_gb} Go` : "non mesurée"],
      ["Carte graphique", gpu ? `${gpu.name} · ${machineGo(gpu.vram_free_gb)} libres sur ${machineGo(gpu.vram_total_gb)}` : "non mesurée"],
      ["Disque", profile.disk && profile.disk.home_free_gb !== null && profile.disk.home_free_gb !== undefined ? `${profile.disk.home_free_gb} Go libres` : "non mesuré"],
    ];
    const table = document.createElement("dl");
    table.className = "machine-profile";
    for (const [label, value] of rows) {
      const term = document.createElement("dt");
      term.textContent = label;
      const desc = document.createElement("dd");
      desc.textContent = value;
      table.appendChild(term);
      table.appendChild(desc);
    }
    host.appendChild(table);

    if (Array.isArray(profile.unknown) && profile.unknown.length) {
      host.appendChild(
        machineLine(`Non mesuré, donc jamais supposé : ${profile.unknown.join(", ")}`, "machine-unknown"),
      );
    }

    const reasons = payload.reasons || [];
    const recommended = payload.entry;
    host.appendChild(machineLine("Modèle recommandé", "machine-title"));
    if (recommended) {
      host.appendChild(machineEntryCard(recommended));
    } else {
      host.appendChild(machineLine("Aucun modèle du catalogue ne tient sur cette machine.", "machine-warning"));
    }
    for (const reason of reasons) host.appendChild(machineLine(reason, "machine-reason"));

    const alternatives = payload.alternatives || [];
    if (alternatives.length) {
      host.appendChild(machineLine("Autres possibilités, avec leur prix", "machine-title"));
      for (const entry of alternatives) {
        host.appendChild(machineEntryCard(entry, { alternative: true }));
      }
    }
    host.setAttribute("aria-busy", "false");
  }

  async function loadMachinePanel() {
    const host = $("machine-panel");
    if (!host) return;
    host.setAttribute("aria-busy", "true");
    host.textContent = "";
    host.appendChild(machineLine("Lecture de la machine par UHB…", "machine-reason"));
    const result = await apiResult("/api/machine/scan", { method: "POST", body: "{}" });
    if (!result.ok) {
      host.textContent = "";
      const body = result.body || {};
      host.appendChild(
        machineLine(
          typeof body.detail === "string" ? body.detail : "UHB n'a pas répondu : le panneau Machine lit son API.",
          "machine-warning",
        ),
      );
      host.setAttribute("aria-busy", "false");
      return;
    }
    machineScan = result.body;
    renderMachinePanel(result.body);
  }

  async function machineDownload(entry, button) {
    // Le clic EST le consentement : le corps porte `accept: true`, et le serveur
    // refuse sans lui. Rien ne part d'un chargement de page.
    button.disabled = true;
    const previous = button.textContent;
    button.textContent = "Demande envoyée…";
    const result = await apiResult("/api/machine/pull", {
      method: "POST",
      body: JSON.stringify({ entry_id: entry.id, accept: true }),
    });
    const host = $("machine-panel");
    if (!result.ok) {
      button.textContent = previous;
      button.disabled = false;
      const detail = result.body && typeof result.body.detail === "string" ? result.body.detail : "téléchargement refusé";
      if (host) host.appendChild(machineLine(detail, "machine-warning"));
      return;
    }
    const info = document.createElement("div");
    info.className = "machine-progress";
    info.dataset.machineRun = result.body.id || "";
    info.textContent = `Téléchargement lancé (${entry.file_gb} Go). Suivi : ${result.body.status_url || "UHB"}`;
    if (host) host.appendChild(info);
    button.textContent = "Téléchargement lancé";
  }

  return { loadMachinePanel, renderMachinePanel, machineEntryCard };
}
