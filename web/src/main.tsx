import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { Api } from "./api";
import { Portal } from "./pages/Portal";
import "./styles.css";
import { UiProvider } from "./ui";

const root = document.getElementById("root");
if (!root) throw new Error("root element missing");
createRoot(root).render(
  <StrictMode>
    {window.location.pathname.startsWith("/portal") ? (
      <Portal />
    ) : (
      <UiProvider api={new Api()}>
        <App />
      </UiProvider>
    )}
  </StrictMode>,
);
