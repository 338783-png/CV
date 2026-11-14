"use strict";
// Lien « Partager sur WhatsApp » : il a besoin de l'adresse du site, connue seulement ici.
// Le message (dans la langue de la page) est dans l'attribut data-partage.
for (const lien of document.querySelectorAll("a[data-partage]")) {
  const prefixe = (location.pathname.match(/^\/(en|it)(?=\/|$)/) || [""])[0];
  lien.href = "https://wa.me/?text=" + encodeURIComponent(lien.dataset.partage + location.origin + prefixe + "/");
  lien.hidden = false;
}
