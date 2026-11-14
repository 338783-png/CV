"use strict";
// Lien « Partager sur WhatsApp » : il a besoin de l'adresse du site, connue seulement ici.
for (const lien of document.querySelectorAll("a[data-partage]")) {
  const message = "Crée ton CV et ta lettre de motivation en PDF, gratuitement : " + location.origin + "/";
  lien.href = "https://wa.me/?text=" + encodeURIComponent(message);
  lien.hidden = false;
}
