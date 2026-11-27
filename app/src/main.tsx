import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource-variable/geist";
import "@fontsource-variable/noto-sans-sc";
import App from "./App";
import "./index.css";
import { reportClientError } from "./lib/api";

// Light or dark follows the system setting, and changes with it.
const dark = window.matchMedia("(prefers-color-scheme: dark)");
const applyScheme = () => document.documentElement.classList.toggle("dark", dark.matches);
applyScheme();
dark.addEventListener("change", applyScheme);

window.addEventListener("error", (ev) => {
  const stack = ev.error instanceof Error && ev.error.stack ? ev.error.stack : "";
  const ctx = ev.filename ? `${ev.filename}:${ev.lineno}:${ev.colno}` : "";
  reportClientError("window.onerror", ev.message || "Uncaught error", stack, ctx);
});

window.addEventListener("unhandledrejection", (ev) => {
  const reason = ev.reason;
  const msg = reason instanceof Error ? reason.message : String(reason ?? "Unhandled rejection");
  const stack = reason instanceof Error && reason.stack ? reason.stack : "";
  reportClientError("unhandledrejection", msg, stack);
});

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
