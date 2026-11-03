import React from "react";
import { createRoot } from "react-dom/client";
import "leaflet/dist/leaflet.css";
import App from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root missing");

createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
