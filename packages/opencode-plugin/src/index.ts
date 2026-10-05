/**
 * OpenCode Plugin for Scraper Swarm.
 *
 * Implements:
 * 1. tool.execute.before guard blocking unauthenticated/direct raw fetch and directing agent to swarm_fetch
 * 2. Slash command /swarm-status
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
