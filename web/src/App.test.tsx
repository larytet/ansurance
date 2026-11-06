import { render, screen } from "@testing-library/react";
import { App } from "./App";

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));

it("shows the server status", async () => {
  const fetcher = (() => json({ status: "ok", version: "0.1.0", profile: "demo" })) as typeof fetch;
  render(<App fetcher={fetcher} />);
  expect(await screen.findByTestId("status")).toHaveTextContent("Server ok, version 0.1.0, profile demo");
});

it("shows an alert when the server is down", async () => {
  const fetcher = (() => json({}, 503)) as typeof fetch;
  render(<App fetcher={fetcher} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("not reachable");
});
