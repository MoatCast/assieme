import { render } from "@testing-library/react";
import { test, expect } from "vitest";
import App from "./App";

test("the app renders", () => {
  const { container } = render(<App />);
  expect(container.firstChild).not.toBeNull();
});
