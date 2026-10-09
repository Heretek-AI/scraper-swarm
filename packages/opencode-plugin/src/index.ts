/**
 * OpenCode Plugin for Scraper Swarm.
 *
 * Implements:
 * 1. tool.execute.before guard blocking unauthenticated/direct raw fetch and directing the agent to the scraper-swarm MCP tools (web_search, fetch_page)
 * 2. Slash command /swarm-status
 *
 * Phase 03-opencode-integration (P2 OpenCode v2 live integration) fetch-guard proof:
 * - file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/scraper_swarm_phase5_roadmap.md::P2-C1-C2-C3
 * - file:///home/john/Projects/scraper-swarm/packages/opencode-plugin/src/index.ts
 * - file:///home/john/Projects/scraper-swarm/apps/panel-web/src/components/workbench/WorkbenchView.tsx
 *
 * Design note (AC4): the OpenCode v2 hook surface cannot re-route a tool call
 * to a different tool, so the guard BLOCKS raw `webfetch`/`fetch` with an
 * error naming the correct swarm MCP tools (the scraper-swarm gateway's
 * `web_search` / `fetch_page`). The Agent Workbench console (`WorkbenchView`) is the equivalent
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
 *
 * Retry2 QA-B P0-4: denylist broadened to network-exfil primitives. Direct
 * tool names python/powershell/pwsh/cmd/http_request (+socket/netcat/nc) are
 * blocked outright; shell/python args carrying /dev/tcp, socket,
 * invoke-webrequest, base64 pipe, or nc are blocked as exfil intent. Plain
 * `bash ls` still passes. Residual risk (novel exfil binaries, obfuscated
 * one-liners the substring list cannot see) is documented in
 * docs/opencode-integration.md §4 — the gateway SSRF pre-deny + Smokescreen
 * remain the enforcement backstop for anything the editor guard misses.
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

// Shell-like hosts whose ARGS are inspected for exfil intent (not blocked
// outright so plain `ls` keeps working).
const SHELL_TOOLS = new Set(["bash", "shell", "exec", "command", "terminal", "sh", "zsh"]);

// Retry2 P0-4: direct-tool substring blocklist (normalized). Long tokens use
// substring so versioned variants (python3, powershell.exe) still match.
const BLOCKED_TOOL_SUBSTRINGS = [
  "fetch",
  "curl",
  "wget",
  "python",
  "powershell",
  "pwsh",
  "httprequest",
  "socket",
  "netcat",
];

// Retry2 P0-4: exact-match blocklist for short names where substring would
// false-positive (e.g. "nc" inside "sync", "cmd" handling kept exact).
const BLOCKED_TOOL_EXACT = new Set(["cmd", "nc", "ncat"]);

function isBlockedTool(normalized: string): boolean {
  for (const part of BLOCKED_TOOL_SUBSTRINGS) {
    if (normalized.includes(part)) return true;
  }
  if (BLOCKED_TOOL_EXACT.has(normalized)) return true;
  return false;
}

function isFetchTool(normalized: string): boolean {
  return isBlockedTool(normalized);
}

function shellArgsLookLikeFetch(args: Record<string, any>): boolean {
  return shellArgsLookLikeExfil(args);
}

function shellArgsLookLikeExfil(args: Record<string, any>): boolean {
  try {
    const blob = JSON.stringify(args || {}).toLowerCase();
    if (
      blob.includes("curl") ||
      blob.includes("wget") ||
      blob.includes("http://") ||
      blob.includes("https://") ||
      blob.includes("webfetch") ||
      blob.includes("httprequest") ||
      blob.includes("http_request")
    )
      return true;
    if (
      blob.includes("python") ||
      blob.includes("powershell") ||
      blob.includes("pwsh") ||
      blob.includes("invoke-webrequest") ||
      blob.includes("invoke-restmethod")
    )
      return true;
    if (blob.includes("/dev/tcp") || blob.includes("socket") || blob.includes("base64"))
      return true;
    if (blob.includes("netcat") || blob.includes("ncat")) return true;
    // "nc" needs word boundaries: "nc host 4444" blocks, "sync files" passes.
    if (/(?:^|[^a-z])nc(?:[^a-z]|$)/.test(blob)) return true;
    // bare "cmd" as a command word (key "command" itself must not trigger).
    if (/(?:^|[^a-z])cmd(?:\.exe)?(?:[^a-z]|$)/.test(blob)) {
      // Avoid matching the JSON key "command": require cmd NOT followed by "mand".
      // The regex above already excludes "command" (cmd followed by "m").
      return true;
    }
    return false;
  } catch {
    return false;
  }
}

function blockedMessage(tool: string): string {
  return (
    `[Scraper Swarm Guard] Direct web fetch via tool '${tool}' is blocked by security policy. ` +
    `Use the scraper-swarm MCP tools (web_search, fetch_page) to route through egress controls and SSRF filters.`
  );
}

export const ScraperSwarmPlugin = async (ctx: PluginContext) => {
  return {
    "tool.execute.before": async (input: { tool: string }, output: { args: Record<string, any> }) => {
      const rawTool = input.tool || "";
      const normalized = normalizeToolName(rawTool);
      // 1. Direct exfil-tool names, case-insensitive (WebFetch/python3/
      // powershell/pwsh/cmd/nc/http_request/socket/...).
      if (isBlockedTool(normalized)) {
        throw new Error(blockedMessage(rawTool));
      }
      // 2. Shell bypass: bash/shell/exec (and python-ish hosts invoked via
      // shell) with exfil-like args (/dev/tcp, socket, base64|sh, nc, ...).
      if (SHELL_TOOLS.has(normalized) || SHELL_TOOLS.has(rawTool.toLowerCase())) {
        if (shellArgsLookLikeExfil(output.args)) {
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
