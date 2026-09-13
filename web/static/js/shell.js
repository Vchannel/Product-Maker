// App shell: theme toggle and mobile navigation.
const root = document.documentElement;

document.getElementById("theme-toggle")?.addEventListener("click", () => {
  const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  try { localStorage.setItem("pm-theme", next); } catch (e) { /* private mode */ }
});

document.getElementById("nav-toggle")?.addEventListener("click", (e) => {
  e.stopPropagation();
  document.body.classList.toggle("nav-open");
});
document.addEventListener("click", (e) => {
  if (document.body.classList.contains("nav-open") && !e.target.closest("#sidebar")) {
    document.body.classList.remove("nav-open");
  }
});
