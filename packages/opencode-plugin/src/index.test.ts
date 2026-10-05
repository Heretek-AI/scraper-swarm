import { describe, it, expect } from "vitest";
import { ScraperSwarmPlugin } from "./index.js";

describe("ScraperSwarmPlugin", () => {
  it("initializes hooks correctly", async () => {
    const plugin = await ScraperSwarmPlugin({});
    expect(plugin["tool.execute.before"]).toBeDefined();
    expect(plugin["command.executed"]).toBeDefined();
  });

  it("blocks direct unauthenticated web fetch and provides remediation message", async () => {
    const plugin = await ScraperSwarmPlugin({});
    const guard = plugin["tool.execute.before"];

    await expect(
      guard({ tool: "webfetch" }, { args: { url: "http://169.254.169.254/latest/meta-data" } })
    ).rejects.toThrowError(/Direct web fetch to .* is blocked by security policy/);

    await expect(
      guard({ tool: "fetch" }, { args: { url: "https://example.com" } })
    ).rejects.toThrowError(/Use the swarm_fetch \/ scraper-swarm MCP tool/);
  });

  it("allows other non-fetch tools to proceed unhindered", async () => {
    const plugin = await ScraperSwarmPlugin({});
    const guard = plugin["tool.execute.before"];

    // Should not throw
    await expect(
      guard({ tool: "read" }, { args: { path: "main.py" } })
    ).resolves.toBeUndefined();
  });
});
