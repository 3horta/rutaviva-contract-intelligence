import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";

// The integration tests render the whole app in jsdom; on a slow CI machine a
// streamed answer can take a few seconds, more than the 1 s default.
configure({ asyncUtilTimeout: 10_000 });
