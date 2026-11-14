"use strict";
// En-tête des pages : si le visiteur est connecté, le lien « Se connecter » devient
// « Mon espace », avec l'initiale de son adresse.
(async () => {
  try {
    const r = await fetch("/api/compte", { credentials: "same-origin" });
    const compte = await r.json();
    if (!compte.connecte) return;
    for (const lien of document.querySelectorAll("a[data-lien-compte]")) {
      const pastille = document.createElement("span");
      pastille.className = "initiale";
      pastille.textContent = compte.email[0].toUpperCase();
      lien.replaceChildren(pastille, "Mon espace");
      lien.title = compte.email;
    }
  } catch { /* hors ligne : le lien reste « Se connecter » */ }
})();
