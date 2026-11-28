// La vue Décision dit qui décide du routage (P2-10), sortie de `app.js` (P2-11).
//
// Frontière : la politique appartient à UHB ; ce module ne classe rien, ne
// calcule aucune chaîne et ne fait qu'afficher l'autorité lue sur
// `/api/uhb/status`. Il reçoit `api` et `el` et n'a accès à rien d'autre.

export function createDecisionView({ api, el }) {
  // P2-10 — qui décide du routage. La politique appartient à UHB ; UH ne classe
  // rien et n'a pas de second avis à afficher. Quand UHB n'est pas consultable,
  // la vue le dit au lieu de laisser croire que le repli vient d'une politique.
  async function decisionAuthority(box) {
    let status = {};
    try {
      status = await api("/api/uhb/status");
    } catch (error) {
      status = { state: "absent" };
    }
    const state = status.state || (status.connected ? "ok" : "absent");
    const line = el("p", state === "ok" ? "decision-authority" : "decision-authority warn");
    line.dataset.uhbState = state;
    if (state === "ok") {
      const mode = String(status.mode || "");
      line.textContent =
        `La politique de routage appartient à UHB${mode ? ` (mode ${mode})` : ""} : ` +
        "le plan et la chaîne viennent de lui, UH les affiche sans les recalculer.";
    } else if (state === "error") {
      line.textContent =
        "UHB est joignable mais en erreur : aucune politique de routage n'est " +
        "consultable pour l'instant. Ce qui suit décrit le tour, il ne l'explique pas.";
    } else {
      line.textContent =
        "UHB n'est pas consulté : sans lui, il n'y a pas de politique de routage. " +
        "Ce qui suit décrit le tour tel qu'il s'est passé, sans chaîne de repli.";
    }
    box.prepend(line);
  }

  return { decisionAuthority };
}
