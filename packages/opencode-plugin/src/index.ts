/**
 * OpenCode Plugin for Scraper Swarm.
 *
 * Implements:
 * 1. tool.execute.before guard blocking unauthenticated/direct raw fetch and directing agent to swarm_fetch
 * 2. Slash command /swarm-status
 *
 * Phase 03-opencode-integration (P2 OpenCode v2 live integration) fetch-guard proof:
 * - file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/scraper_swarm_phase5_roadmap.md::P2-C1-C2-C3
 * - file:///home/john/Projects/scraper-swarm/packages/opencode-plugin/src/index.ts
 * - file:///home/john/Projects/scraper-swarm/apps/panel-web/src/components/workbench/WorkbenchView.tsx
 *
 * Design note (AC4): the OpenCode v2 hook surface cannot re-route a tool call
 * to a different tool, so the guard BLOCKS raw `webfetch`/`fetch` with an
 * error naming the correct swarm MCP tool (`swarm_fetch` / scraper-swarm
 * gateway). The Agent Workbench console (`WorkbenchView`) is the equivalent
 * interactive path: it POSTs JSON-RPC `tools/call` to `/mcp` directly, so its
 * traffic always flows through gateway auth, scope checks, SSRF pre-deny,
 * and hash-chained audit. Unit proof lives in `src/index.test.ts` (vitest).
 */

export interface PluginContext {
  project?: any;
  client?: any;
  $?: any;
  directory?: string;
  worktree?: string;
}

export const ScraperSwarmPlugin = async (ctx: PluginContext) => {
  return {
    "tool.execute.before": async (input: { tool: string }, output: { args: Record<string, any> }) => {
      // Guard against direct unmonitored agent web fetching:
      // If agent attempts raw web fetch, block it and direct them to swarm_fetch / MCP gateway
      if (input.tool === "webfetch" || input.tool === "fetch") {
        const url = output.args?.url || output.args?.uri || "unknown";
        throw new Error(
          `[Scraper Swarm Guard] Direct web fetch to "${url}" is blocked by security policy. ` +
          `Use the swarm_fetch / scraper-swarm MCP tool to route through egress controls and SSRF filters.`
        );
      }
    },

    "command.executed": async ({ command, args }: { command: string; args: string[] }) => {
      if (command === "/swarm-status") {
        console.log("[Scraper Swarm] Status: Connected to local swarm MCP gateway.");
      }
    },
  };
};

export default ScraperSwarmPlugin;
