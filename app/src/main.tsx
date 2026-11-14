import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import MenuBar from "./MenuBar";
import { applyTheme, followTheme, savedTheme } from "./Appearance";
import { watchForUpdates } from "./updates";

applyTheme(savedTheme()); // the page already has it from index.html; this sets the native window to match
followTheme();
// The main window checks; the menu bar panel is a second page of the same app.
if (location.search !== "?menu" && !import.meta.env.DEV) watchForUpdates();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    {location.search === "?menu" ? <MenuBar /> : <App />}
  </React.StrictMode>,
);
