import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource-variable/geist";
import "@fontsource-variable/noto-sans-sc";
import App from "./App";
import "./index.css";

// Light or dark follows the system setting, and changes with it.
const dark = window.matchMedia("(prefers-color-scheme: dark)");
const applyScheme = () => document.documentElement.classList.toggle("dark", dark.matches);
applyScheme();
dark.addEventListener("change", applyScheme);

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
