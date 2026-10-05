import { useState } from "react";
import {
  Cpu,
  Key,
  Play,
  Copy,
  Check,
  Trash2,
  Code,
  Terminal,
} from "lucide-react";
import { AgentKey } from "../../types";
import { AuthRequiredCard } from "../ui/AuthRequiredCard";

interface WorkbenchViewProps {
  authenticated: boolean;
  agentKeys: AgentKey[];
  onOpenLogin: () => void;
  onCreateKey: (name: string, scopes: string[], rpm: number) => Promise<any>;
  onRevokeKey: (id: string) => Promise<void>;
}

export const WorkbenchView: React.FC<WorkbenchViewProps> = ({
  authenticated,
  agentKeys,
  onOpenLogin,
  onCreateKey,
  onRevokeKey,
}) => {
  // MCP Playground states
  const [tool, setTool] = useState<"search" | "scrape">("search");
  const [query, setQuery] = useState<string>("open source search cluster architecture");
  const [url, setUrl] = useState<string>("https://example.com");
  const [executing, setExecuting] = useState<boolean>(false);
  const [output, setOutput] = useState<string | null>(null);

  // Key creation states
  const [showKeyModal, setShowKeyModal] = useState<boolean>(false);
  const [keyName, setKeyName] = useState<string>("");
  const [keyScopes] = useState<string[]>(["search", "scrape"]);
  const [keyRpm, setKeyRpm] = useState<number>(60);
  const [issuedKey, setIssuedKey] = useState<any>(null);
  const [copiedKey, setCopiedKey] = useState<boolean>(false);

  async function handleExecuteMcp() {
    setExecuting(true);
    setOutput(null);
    try {
      const endpoint = tool === "search" ? "/search" : "/scrape";
      const payload = tool === "search" ? { query } : { url };

      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      setOutput(JSON.stringify(data, null, 2));
    } catch (e: any) {
      setOutput(`Execution error: ${e.message}`);
    } finally {
      setExecuting(false);
    }
  }

  async function handleKeySubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!keyName) return;
    const res = await onCreateKey(keyName, keyScopes, keyRpm);
    if (res) {
      setIssuedKey(res);
      setKeyName("");
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-center space-x-2">
          <Cpu className="h-5 w-5 text-primary" />
          <h2 className="text-base font-bold uppercase tracking-wider text-foreground">
            Agent Workbench & MCP Integration
          </h2>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">
          Execute live Model Context Protocol (MCP) tools and manage scoped bearer keys for autonomous agents.
        </p>
      </div>

      {!authenticated && (
        <AuthRequiredCard
          description="Agent API key provisioning and interactive MCP runtime testing require verified administrator TOTP authentication."
          buttonText="Sign In to Access Workbench"
          onOpenLogin={onOpenLogin}
        />
      )}

      {authenticated && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Left Column: Interactive MCP Playground */}
          <div className="p-6 rounded-xl border border-border bg-card/40 glass space-y-4 shadow-sm flex flex-col justify-between">
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <Terminal className="h-4 w-4 text-primary" />
                  <h3 className="text-xs font-bold uppercase tracking-wider text-foreground">
                    Interactive MCP Tool Console
                  </h3>
                </div>
                <span className="text-[10px] text-status-ok font-mono font-semibold">JSON-RPC 2.0</span>
              </div>

              {/* Tool Picker */}
              <div className="flex items-center space-x-2 p-1 rounded-lg bg-secondary/30 border border-border text-xs">
                <button
                  onClick={() => setTool("search")}
                  className={`flex-1 py-1.5 rounded-md font-semibold transition-all ${
                    tool === "search" ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground"
                  }`}
                >
                  deep_research (SearXNG)
                </button>
                <button
                  onClick={() => setTool("scrape")}
                  className={`flex-1 py-1.5 rounded-md font-semibold transition-all ${
                    tool === "scrape" ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground"
                  }`}
                >
                  stealth_scrape (Crawl4AI)
                </button>
              </div>

              {/* Tool Inputs */}
              {tool === "search" ? (
                <div>
                  <label className="text-muted-foreground block text-[11px] mb-1">Search Query</label>
                  <input
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    className="w-full bg-input border border-border rounded-lg px-3 py-2 text-xs font-mono"
                    placeholder="Enter search query..."
                  />
                </div>
              ) : (
                <div>
                  <label className="text-muted-foreground block text-[11px] mb-1">Target URL</label>
                  <input
                    type="text"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    className="w-full bg-input border border-border rounded-lg px-3 py-2 text-xs font-mono"
                    placeholder="https://example.com"
                  />
                </div>
              )}

              <button
                onClick={handleExecuteMcp}
                disabled={executing}
                className="w-full py-2 bg-primary text-primary-foreground font-semibold text-xs rounded-lg hover:bg-primary/90 transition-all flex items-center justify-center gap-1.5 shadow-md shadow-primary/20 disabled:opacity-50"
              >
                <Play className={`h-3.5 w-3.5 ${executing ? "animate-pulse" : ""}`} />
                <span>{executing ? "Invoking MCP Gateway..." : `Execute ${tool === "search" ? "deep_research" : "stealth_scrape"}`}</span>
              </button>
            </div>

            {/* Output Panel */}
            <div className="mt-4 pt-4 border-t border-border/50">
              <div className="text-[10px] uppercase font-bold text-muted-foreground mb-1">
                Gateway Response Payload
              </div>
              <div className="p-3.5 rounded-lg bg-background/90 border border-border font-mono text-[11px] text-foreground max-h-48 overflow-y-auto leading-relaxed whitespace-pre-wrap select-all">
                {executing ? (
                  <span className="text-muted-foreground">Streaming response through Smokescreen proxy...</span>
                ) : output ? (
                  output
                ) : (
                  <span className="text-muted-foreground/60">Press Execute above to inspect JSON-RPC output.</span>
                )}
              </div>
            </div>
          </div>

          {/* Right Column: Scoped API Key Manager */}
          <div className="p-6 rounded-xl border border-border bg-card/40 glass space-y-4 shadow-sm flex flex-col justify-between">
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <Key className="h-4 w-4 text-primary" />
                  <h3 className="text-xs font-bold uppercase tracking-wider text-foreground">
                    Agent API Keys ({agentKeys.length})
                  </h3>
                </div>
                <button
                  onClick={() => setShowKeyModal(true)}
                  className="px-3 py-1 bg-primary text-primary-foreground text-xs font-semibold rounded-lg hover:bg-primary/90 transition-colors shadow-sm"
                >
                  + Issue Key
                </button>
              </div>

              {/* Newly Issued Key Banner */}
              {issuedKey && (
                <div className="p-4 rounded-lg bg-status-ok/10 border border-status-ok/40 space-y-2 text-xs">
                  <div className="font-bold text-status-ok flex items-center justify-between">
                    <span>Key Generated Successfully</span>
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(issuedKey.raw_key);
                        setCopiedKey(true);
                        setTimeout(() => setCopiedKey(false), 2000);
                      }}
                      className="text-foreground hover:text-primary flex items-center gap-1 text-[11px]"
                    >
                      {copiedKey ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                      <span>{copiedKey ? "Copied" : "Copy"}</span>
                    </button>
                  </div>
                  <div className="p-2 bg-black/40 rounded font-mono text-[11px] break-all select-all text-foreground">
                    {issuedKey.raw_key}
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    Store this bearer token safely. It will not be shown again.
                  </div>
                </div>
              )}

              {/* Issued Keys Table */}
              {agentKeys.length > 0 ? (
                <div className="divide-y divide-border/40 max-h-56 overflow-y-auto">
                  {agentKeys.map((k) => (
                    <div key={k.id} className="py-2.5 flex items-center justify-between text-xs">
                      <div>
                        <div className="font-semibold text-foreground">{k.name}</div>
                        <div className="text-[10px] font-mono text-muted-foreground flex items-center gap-2">
                          <span>{k.key_prefix}</span>
                          <span>•</span>
                          <span>{k.rate_limit_rpm} RPM</span>
                        </div>
                      </div>
                      <button
                        onClick={() => onRevokeKey(k.id)}
                        className="p-1.5 text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded transition-colors"
                        title="Revoke key"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8 text-muted-foreground text-xs border border-dashed border-border rounded-xl">
                  No active agent API keys. Click "+ Issue Key" to authorize an AI agent.
                </div>
              )}
            </div>

            {/* OpenCode Integration Snippet */}
            <div className="pt-4 border-t border-border/50 text-xs space-y-1.5">
              <div className="font-semibold text-foreground flex items-center gap-1.5">
                <Code className="h-3.5 w-3.5 text-primary" /> OpenCode Configuration
              </div>
              <p className="text-[11px] text-muted-foreground">
                Paste issued bearer tokens into your <code>opencode.json</code> under the <code>@scraper-swarm/opencode-plugin</code> block.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Issue Key Modal */}
      {showKeyModal && (
        <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="w-full max-w-md bg-card border border-primary/40 rounded-xl p-6 space-y-4 shadow-2xl glass">
            <h3 className="text-sm font-bold uppercase tracking-wider text-foreground">
              Issue Scoped Agent Bearer Token
            </h3>
            <form onSubmit={handleKeySubmit} className="space-y-4 text-xs">
              <div>
                <label className="text-muted-foreground block mb-1">Key / Agent Name</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. OpenCode Assistant"
                  value={keyName}
                  onChange={(e) => setKeyName(e.target.value)}
                  className="w-full bg-input border border-border rounded-lg px-3 py-2 text-xs font-mono"
                />
              </div>

              <div>
                <label className="text-muted-foreground block mb-1">Rate Limit (Requests / Min)</label>
                <input
                  type="number"
                  min={1}
                  max={1000}
                  value={keyRpm}
                  onChange={(e) => setKeyRpm(Number(e.target.value))}
                  className="w-full bg-input border border-border rounded-lg px-3 py-2 text-xs font-mono"
                />
              </div>

              <div className="flex items-center justify-end space-x-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowKeyModal(false)}
                  className="px-4 py-2 bg-secondary text-foreground text-xs rounded-lg hover:bg-secondary/80 border border-border"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-primary text-primary-foreground font-semibold text-xs rounded-lg hover:bg-primary/90"
                >
                  Generate Bearer Key
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
