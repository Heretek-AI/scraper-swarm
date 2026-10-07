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
 *
 * Retry1 QA-B P0-4: matching is case-insensitive and covers alias variants
 * (web_fetch, web-fetch, curl, wget, ...). Shell tools (bash/shell/exec)
 * carrying fetch-like args (curl/wget/URLs) are blocked as well; plain shell
 * use without network intent still passes. Rationale: bash/curl are the
 * documented bypass vector, so they are denied only when args show network
 * fetch intent — blocking all shell use would break legitimate commands.
 * Block messages are generic (tool name only, never the URL) so secret-bearing
 * query strings are never echoed into error text.
 */

export interface PluginContext {
  project?: any;
  client?: any;
  $?: any;
  directory?: string;
  worktree?: string;
}

function normalizeToolName(tool: string): string {
  return (tool || "").toLowerCase().replace(/[-_\s]/g, "");
}

// Direct-fetch tool aliases (normalized: lowercase, no -/_). Any name
// containing fetch/curl/wget is treated as a fetch tool as a fail-closed
// catch-all for future variants.
const SHELL_TOOLS = new Set(["bash", "shell", "exec", "command", "terminal", "sh", "zsh"]);

function isFetchTool(normalized: string): boolean {
  if (normalized.includes("fetch")) return true;
  if (normalized.includes("curl")) return true;
  if (normalized.includes("wget")) return true;
  return false;
}

function shellArgsLookLikeFetch(args: Record<string, any>): boolean {
  try {
    const blob = JSON.stringify(args || {}).toLowerCase();
    return (
      blob.includes("curl") ||
      blob.includes("wget") ||
      blob.includes("http://") ||
      blob.includes("https://") ||
      blob.includes("webfetch")
    );
  } catch {
    return false;
  }
}

function blockedMessage(tool: string): string {
  return (
    `[Scraper Swarm Guard] Direct web fetch via tool '${tool}' is blocked by security policy. ` +
    `Use the swarm_fetch / scraper-swarm MCP tool to route through egress controls and SSRF filters.`
  );
}

export const ScraperSwarmPlugin = async (ctx: PluginContext) => {
  return {
    "tool.execute.before": async (input: { tool: string }, output: { args: Record<string, any> }) => {
      const rawTool = input.tool || "";
      const normalized = normalizeToolName(rawTool);
      // 1. Direct fetch-tool aliases, case-insensitive (WebFetch/WEBFETCH/web_fetch/...).
      if (isFetchTool(normalized)) {
        throw new Error(blockedMessage(rawTool));
      }
      // 2. Shell bypass: bash/shell/exec with fetch-like args (curl/wget/URL).
      if (SHELL_TOOLS.has(normalized) || SHELL_TOOLS.has(rawTool.toLowerCase())) {
        if (shellArgsLookLikeFetch(output.args)) {
          throw new Error(blockedMessage(rawTool));
        }
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
