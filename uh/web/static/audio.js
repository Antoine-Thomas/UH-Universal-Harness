// La voix de l'interface, sortie de `app.js` (P2-11).
//
// Frontière : le module ne lit ni `state` ni le document global. Il reçoit cinq
// choses — `api`, `el`, `feedbackText`, `humanizeError`, `drawAudio` — et rend le
// bouton « Écouter ». Tout le reste (voix du navigateur d'abord, repli serveur,
// accords explicites) est ici : c'est un domaine, pas une collection d'aides.

export function createAudio({
  api,
  el,
  feedbackText,
  humanizeError,
  drawAudio,
  currentSessionId,
  voiceConsentGiven,
  heavyConsentGiven,
}) {
  // La voix du navigateur lit par défaut : gratuite, instantanée, et le texte ne
  // quitte pas la machine. Le serveur n'est appelé que si elle ne sait pas parler.
  function browserSynth() {
    return typeof window !== "undefined" && window.speechSynthesis ? window.speechSynthesis : null;
  }

  // Choisir une voix : française de préférence, et **locale** par défaut. Sous Edge,
  // les voix « Natural (en ligne) » envoient le texte chez Microsoft — `localService`
  // vaut alors false (MDN, Web Speech API). Une voix en ligne n'est utilisée que si
  // aucune voix locale française n'existe, et l'interface le dit.
  function frenchVoice(voices) {
    const list = Array.isArray(voices) ? voices : [];
    let saved = "";
    try {
      saved = window.localStorage.getItem("uh.voice") || "";
    } catch (error) {
      saved = "";
    }
    if (saved) {
      const chosen = list.find((voice) => voice && voice.name === saved);
      if (chosen && chosen.localService !== false) return chosen;
    }
    const french = list.filter((voice) => /^fr([-_]|$)/i.test(String((voice && voice.lang) || "")));
    const localFrench = french.find((voice) => voice.localService !== false);
    if (localFrench) return localFrench;
    const anyLocal = list.find((voice) => voice && voice.localService !== false);
    return french[0] || anyLocal || null;
  }

  function voiceIsRemote(voice) {
    return Boolean(voice) && voice.localService === false;
  }

  // Le navigateur remplit sa liste de voix *après* le premier appel : `getVoices()`
  // rend souvent un tableau vide au premier clic, puis `voiceschanged` arrive (MDN).
  function whenVoicesReady(synth, callback, timeoutMs = 1500) {
    const first = typeof synth.getVoices === "function" ? synth.getVoices() || [] : [];
    if (first.length) {
      callback(first);
      return;
    }
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      if (typeof synth.removeEventListener === "function") {
        synth.removeEventListener("voiceschanged", finish);
      }
      callback(typeof synth.getVoices === "function" ? synth.getVoices() || [] : []);
    };
    if (typeof synth.addEventListener === "function") synth.addEventListener("voiceschanged", finish);
    setTimeout(finish, timeoutMs);
  }

  // Une phrase = une prise de parole : on peut arrêter entre deux phrases, et la
  // prosodie ne traverse pas trente lignes. Une phrase plus longue que la borne du
  // serveur (2 000 caractères) est coupée à la borne, comme le fait le plan serveur.
  function sentences(text, limit = 2000) {
    const parts = [];
    String(text || "")
      .split(/(?<=[.!?…;:])\s+/)
      .forEach((block) => {
        const piece = block.trim();
        if (!piece) return;
        if (piece.length <= limit) {
          parts.push(piece);
          return;
        }
        for (let at = 0; at < piece.length; at += limit) parts.push(piece.slice(at, at + limit));
      });
    return parts;
  }

  // Rend un lecteur { stop } quand le navigateur sait parler, sinon null : l'appelant
  // choisit alors le serveur, au lieu de croire que rien n'a été dit.
  function readAloud(text, { onDone, onError, onRemote }) {
    const synth = browserSynth();
    if (!synth || typeof window.SpeechSynthesisUtterance !== "function") return null;
    const parts = sentences(text);
    if (!parts.length) return null;
    let voice = null;
    let stopped = false;
    let index = 0;
    const next = () => {
      if (stopped) return;
      if (index >= parts.length) {
        onDone();
        return;
      }
      if (index === 0 && !voice) voice = frenchVoice([]); // aucune voix : on lit quand même
      const line = new window.SpeechSynthesisUtterance(parts[index]);
      index += 1;
      if (voice) line.voice = voice;
      line.lang = (voice && voice.lang) || "fr-FR";
      line.onend = next;
      line.onerror = (event) => {
        if (!stopped) onError(event);
      };
      synth.speak(line);
    };
    // Les voix arrivent parfois après le clic : on démarre la première phrase dès
    // qu'elles sont là, sans bloquer l'interface.
    whenVoicesReady(synth, (voices) => {
      if (stopped) return;
      voice = frenchVoice(voices);
      if (voiceIsRemote(voice) && typeof onRemote === "function") onRemote(voice);
      next();
    });
    return {
      stop() {
        stopped = true;
        try {
          synth.cancel();
        } catch (error) {
          // rien à annuler : le lecteur est déjà arrêté
        }
      },
    };
  }

  // La décision de bascule est prise par le serveur (`capabilities.fallback`) : le
  // client la lit au lieu d'écrire sa propre phrase. Une seule requête par session.
  let audioCapsCache = null;

  async function audioCapabilities() {
    if (audioCapsCache) return audioCapsCache;
    try {
      audioCapsCache = await api("/api/audio/capabilities");
    } catch (error) {
      audioCapsCache = {};
    }
    return audioCapsCache;
  }

  // Read an answer aloud. The synthesis is what creates the sound, so the player only
  // appears once a file really exists - the conditional rule, applied to its origin.
  function speakBox(box, text) {
    const readable = feedbackText(text);
    // Pas de prose, pas de bouton : un appel à /api/audio/speak avec un texte vide
    // produisait « voix impossible : text is required », trois fois de suite.
    if (readable.words < 3) return null;
    const bar = el("div", "answer-audio");
    const button = el("button", "tool-artifact-btn", "Écouter");
    button.type = "button";
    button.title = "lire cette réponse à voix haute";
    button.setAttribute("aria-label", "lire cette réponse à voix haute");
    // Un seul nœud de statut, réutilisé : la phrase d'erreur remplace, elle ne s'empile pas.
    const status = el("span", "tool-artifact-status", "");
    let reading = null;
    let remoteWarned = false;
    bar.appendChild(button);
    bar.appendChild(status);

    const reset = () => {
      reading = null;
      button.textContent = "Écouter";
      button.disabled = false;
    };

    const speakOnServer = async () => {
      button.disabled = true;
      status.textContent = "synthèse…";
      try {
        // Le découpage est demandé au serveur (même règle que `speech_plan`) : un
        // texte long se lit en morceaux au lieu de partir entier. Au-delà du plafond,
        // le serveur refuse et le dit — aucune troncature silencieuse.
        const plan = await api("/api/audio/plan", {
          method: "POST",
          body: JSON.stringify({ text: String(text || "") }),
        });
        const clips = [];
        for (const part of plan.parts) {
          clips.push(
            await api("/api/audio/speak", {
              method: "POST",
              body: JSON.stringify({
                text: part,
                session_id: currentSessionId() || "",
                // Deux accords explicites, jamais supposés : le cloud et le GPU.
                consent: voiceConsentGiven(),
                heavy_consent: heavyConsentGiven(),
              }),
            })
          );
        }
        status.textContent = plan.count > 1 ? `${plan.count} morceaux lus` : "";
        drawAudio(box, clips, { sessionId: currentSessionId() });
        bar.remove();
      } catch (error) {
        const failure = humanizeError(error.message, "ui");
        // La bascule (« repli navigateur ») vient de `capabilities.fallback` : le
        // serveur dit quoi faire et pourquoi, le client ne réinvente pas la phrase.
        const caps = await audioCapabilities();
        const fallback = (caps && caps.fallback) || {};
        const advice = fallback.engine === "browser" && fallback.info ? ` — ${fallback.info}` : "";
        // Hors ligne : la phrase du serveur qui ne répond plus, et un bouton utile.
        // Sinon : le préfixe « voix impossible » reste, il dit de quoi on parle.
        status.textContent = failure.offline
          ? failure.text
          : `voix impossible : ${failure.text}${advice}`;
        if (failure.offline) {
          const reload = el("button", "data-button", "Recharger");
          reload.type = "button";
          reload.addEventListener("click", () => window.location.reload());
          status.appendChild(document.createTextNode(" "));
          status.appendChild(reload);
        }
        reset();
      }
    };

    button.addEventListener("click", () => {
      if (reading) {
        reading.stop();
        reset();
        status.textContent = "";
        return;
      }
      if (button.disabled) return;
      const current = feedbackText(text);
      if (current.words < 3) {
        status.textContent = "texte trop court pour être lu";
        return;
      }
      const reader = readAloud(String(text || ""), {
        onRemote: () => {
          // Une voix en ligne envoie le texte chez le fournisseur du navigateur :
          // on le dit avant la première phrase, pas après.
          remoteWarned = true;
          status.textContent =
            "voix en ligne : le texte part chez le fournisseur du navigateur";
        },
        onDone: () => {
          reset();
          status.textContent = "";
        },
        onError: () => {
          // La voix du navigateur a échoué : on le dit, et on bascule sur le serveur.
          reset();
          status.textContent = "voix du navigateur indisponible : repli serveur";
          speakOnServer();
        },
      });
      if (!reader) {
        speakOnServer();
        return;
      }
      reading = reader;
      button.textContent = "Arrêter";
      if (!remoteWarned) status.textContent = "lecture par la voix du navigateur";
    });
    box.appendChild(bar);
    return bar;
  }
  return { speakBox, browserSynth, frenchVoice, sentences };
}
