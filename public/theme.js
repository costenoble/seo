// Appliqué avant l'affichage pour éviter un flash de couleurs : thème choisi par l'utilisateur, sinon celui du système.
(function () {
  try { var t = localStorage.getItem("vigie-theme"); if (t === "dark" || t === "light") document.documentElement.dataset.theme = t; } catch (e) {}
})();
