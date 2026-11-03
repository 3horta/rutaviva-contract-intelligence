import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { USE_MOCKS } from "./config";
import "./styles/tokens.css";
import "./styles/base.css";

async function prepare(): Promise<void> {
  // A constant at build time: without mocks this import is dropped from the bundle.
  if (USE_MOCKS) {
    const { installMockApi } = await import("./mocks/server");
    installMockApi();
  }
}

void prepare().then(() => {
  createRoot(document.getElementById("root") as HTMLElement).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
