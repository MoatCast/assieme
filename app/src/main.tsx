import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import MenuBar from "./MenuBar";
import { applyTheme, followTheme, savedTheme } from "./Appearance";

applyTheme(savedTheme()); // the page already has it from index.html; this sets the native window to match
followTheme();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    {location.search === "?menu" ? <MenuBar /> : <App />}
  </React.StrictMode>,
);
