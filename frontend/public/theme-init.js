// Applies the theme before React renders, so dark mode never flashes light on
// load. Same storage key and attribute as src/theme/useTheme.ts. A file and not
// an inline script, so the Content-Security-Policy can be script-src 'self'.
(function () {
  try {
    var saved = localStorage.getItem("rutaviva.theme");
    var dark = saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  } catch (e) {
    // Storage blocked: the app falls back to the light theme.
  }
})();
