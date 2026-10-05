import { useEffect, useState } from "react";
import {
  Server,
  Key,
  Terminal,
  Activity,
  CheckCircle2,
  AlertTriangle,
  Lock,
  Layers,
  Copy,
  RefreshCw,
} from "lucide-react";

interface ServiceItem {
  id: string;
  name: string;
  tier: string;
  license: { spdx: string; notice?: string };
  verified: boolean;
}

interface AgentKey {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  rate_limit_rpm: number;
}

export default function App() {
  const [setupCompleted, setSetupCompleted] = useState<boolean | null>(null);
  const [activeTab, setActiveTab] = useState<"dashboard" | "catalog" | "agents" | "wizard">("dashboard");

  // Auth & Wizard states
  const [bootstrapToken, setBootstrapToken] = useState("");
  const [adminUsername, setAdminUsername] = useState("admin");
  const [totpSecret, setTotpSecret] = useState<string | null>(null);
  const [totpUri, setTotpUri] = useState<string | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  // Catalog & Agent states
  const [catalog, setCatalog] = useState<ServiceItem[]>([]);
  const [agentKeys, setAgentKeys] = useState<AgentKey[]>([]);
  const [newKeyName, setNewKeyName] = useState("");
  const [createdSnippet, setCreatedSnippet] = useState<string | null>(null);
  const [selectedServices] = useState<string[]>(["searxng", "crawl4ai"]);

  useEffect(() => {
    checkStatus();
  }, []);

  async function checkStatus() {
    try {
      const res = await fetch("/auth/status");
      const data = await res.json();
      setSetupCompleted(data.setup_completed);
      if (!data.setup_completed) {
        setActiveTab("wizard");
      } else {
        // Check if already authenticated via session cookie
        const me = await fetch("/auth/me");
        if (me.ok) {
          setAuthenticated(true);
          loadDashboardData();
        }
      }
    } catch (e) {
      console.error("API unreachable", e);
    }
  }

  async function loadDashboardData() {
    try {
      const catRes = await fetch("/services/catalog");
      if (catRes.ok) setCatalog(await catRes.json());

      const keysRes = await fetch("/agents/keys");
      if (keysRes.ok) setAgentKeys(await keysRes.json());
    } catch (e) {
      console.error("Error loading dashboard data", e);
    }
  }

  async function handleBootstrapInit() {
    setStatusMessage("");
    try {
      const res = await fetch("/auth/bootstrap-init", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: bootstrapToken, admin_username: adminUsername }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Bootstrap initialization failed");

      setTotpSecret(data.totp_secret);
      setTotpUri(data.totp_uri);
    } catch (e: any) {
      setStatusMessage(e.message);
    }
  }

  async function handleVerifyTotp() {
    setStatusMessage("");
    try {
      const res = await fetch("/auth/verify-totp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: adminUsername, code: totpCode }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "TOTP verification failed");

      setAuthenticated(true);
      setSetupCompleted(true);
      setActiveTab("dashboard");
      loadDashboardData();
    } catch (e: any) {
      setStatusMessage(e.message);
    }
  }

  async function handleDeploySelected() {
    setStatusMessage("Deploying stack via swarmd...");
    try {
      const payload = selectedServices.map((id) => ({ service_id: id, profile: "standard" }));
      const res = await fetch("/services/deploy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Deploy failed");

      setStatusMessage("Deployment applied successfully via swarmd.");
      setActiveTab("dashboard");
    } catch (e: any) {
      setStatusMessage(e.message);
    }
  }

  async function handleCreateAgentKey() {
    if (!newKeyName) return;
    try {
      const res = await fetch("/agents/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newKeyName, scopes: ["search", "scrape"] }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Key creation failed");

      setCreatedSnippet(JSON.stringify(data.opencode_snippet, null, 2));
      setNewKeyName("");
      loadDashboardData();
    } catch (e: any) {
      alert(e.message);
    }
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground font-mono">
      {/* Left Navigation Console Rail */}
      <aside className="w-64 border-r border-border bg-card/60 flex flex-col justify-between p-4 z-10 glass">
        <div className="space-y-6">
          <div className="flex items-center space-x-3 px-2">
            <div className="h-8 w-8 rounded border border-primary/50 bg-primary/10 flex items-center justify-center text-primary corruption-glow">
              <Activity className="h-5 w-5" />
            </div>
            <div>
              <h1 className="font-bold text-sm tracking-wider uppercase text-foreground">
                Scraper Swarm
              </h1>
              <span className="text-[10px] text-muted-foreground uppercase tracking-widest block">
                Security Console
              </span>
            </div>
          </div>

          <nav className="space-y-1">
            <button
              onClick={() => setActiveTab("dashboard")}
              className={`w-full flex items-center space-x-3 px-3 py-2 text-xs rounded transition-colors ${
                activeTab === "dashboard"
                  ? "bg-primary/20 text-primary border border-primary/40"
                  : "text-muted-foreground hover:bg-secondary/40 hover:text-foreground"
              }`}
            >
              <Server className="h-4 w-4" />
              <span>Command Deck</span>
            </button>

            <button
              onClick={() => setActiveTab("catalog")}
              className={`w-full flex items-center space-x-3 px-3 py-2 text-xs rounded transition-colors ${
                activeTab === "catalog"
                  ? "bg-primary/20 text-primary border border-primary/40"
                  : "text-muted-foreground hover:bg-secondary/40 hover:text-foreground"
              }`}
            >
              <Layers className="h-4 w-4" />
              <span>Engine Catalog</span>
            </button>

            <button
              onClick={() => setActiveTab("agents")}
              className={`w-full flex items-center space-x-3 px-3 py-2 text-xs rounded transition-colors ${
                activeTab === "agents"
                  ? "bg-primary/20 text-primary border border-primary/40"
                  : "text-muted-foreground hover:bg-secondary/40 hover:text-foreground"
              }`}
            >
              <Key className="h-4 w-4" />
              <span>Agent Connections</span>
            </button>

            <button
              onClick={() => setActiveTab("wizard")}
              className={`w-full flex items-center space-x-3 px-3 py-2 text-xs rounded transition-colors ${
                activeTab === "wizard"
                  ? "bg-primary/20 text-primary border border-primary/40"
                  : "text-muted-foreground hover:bg-secondary/40 hover:text-foreground"
              }`}
            >
              <Terminal className="h-4 w-4" />
              <span>Install Wizard</span>
            </button>
          </nav>
        </div>

        {/* Security Vitals Pill */}
        <div className="p-3 rounded border border-border/80 bg-background/50 space-y-2 text-[11px]">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Security Posture</span>
            <span className="text-status-ok font-bold flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-status-ok inline-block animate-pulse"></span>
              HARDENED
            </span>
          </div>
          <div className="text-[10px] text-muted-foreground">
            Egress: <span className="text-foreground">Default-Deny (SSRF Guard)</span>
          </div>
        </div>
      </aside>

      {/* Main View Area */}
      <main className="flex-1 flex flex-col h-full overflow-y-auto">
        {/* Top Header Bar */}
        <header className="h-14 border-b border-border px-6 flex items-center justify-between glass">
          <div className="flex items-center space-x-3 text-xs">
            <span className="text-muted-foreground">SYSTEM /</span>
            <span className="text-foreground uppercase tracking-wider font-semibold">
              {activeTab}
            </span>
          </div>

          <div className="flex items-center space-x-4 text-xs">
            <span className="text-muted-foreground">
              Stack: <span className={setupCompleted ? "text-status-ok" : "text-status-warn"}>{setupCompleted ? "Initialized" : "Pending Setup"}</span>
            </span>
            <span className="h-4 w-[1px] bg-border"></span>
            <span className="text-muted-foreground">
              User: <span className="text-foreground">{authenticated ? adminUsername : "Guest"}</span>
            </span>
            <span className="h-4 w-[1px] bg-border"></span>
            <span className="text-muted-foreground flex items-center gap-1">
              <Lock className="h-3 w-3 text-primary" /> HTTPS Isolated
            </span>
          </div>
        </header>

        {/* Dynamic Content */}
        <div className="p-8 space-y-6 max-w-6xl w-full mx-auto">
          {/* Status Alert Banner */}
          {statusMessage && (
            <div className="p-3 rounded bg-secondary/80 border border-primary/50 text-xs flex items-center space-x-2">
              <AlertTriangle className="h-4 w-4 text-primary" />
              <span>{statusMessage}</span>
            </div>
          )}

          {/* COMMAND DECK VIEW */}
          {activeTab === "dashboard" && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="p-4 rounded border border-border bg-card blood-border">
                  <div className="text-xs text-muted-foreground uppercase">Installed Engines</div>
                  <div className="text-2xl font-bold mt-1 text-foreground">
                    {selectedServices.length} Active
                  </div>
                  <div className="text-[10px] text-muted-foreground mt-2">
                    SearXNG + Crawl4AI Running
                  </div>
                </div>

                <div className="p-4 rounded border border-border bg-card blood-border">
                  <div className="text-xs text-muted-foreground uppercase">Egress Protection</div>
                  <div className="text-2xl font-bold mt-1 text-status-ok flex items-center gap-2">
                    <CheckCircle2 className="h-5 w-5" /> Active
                  </div>
                  <div className="text-[10px] text-muted-foreground mt-2">
                    Smokescreen SSRF Proxy (0.0.0.0:4750)
                  </div>
                </div>

                <div className="p-4 rounded border border-border bg-card blood-border">
                  <div className="text-xs text-muted-foreground uppercase">Agent Keys</div>
                  <div className="text-2xl font-bold mt-1 text-foreground">
                    {agentKeys.length} Issued
                  </div>
                  <div className="text-[10px] text-muted-foreground mt-2">
                    Scoped Bearer Tokens
                  </div>
                </div>
              </div>

              {/* Running Engines Table */}
              <div className="p-6 rounded border border-border bg-card space-y-4">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-bold uppercase tracking-wider">Engine Stack Status</h2>
                  <div className="flex items-center space-x-2">
                    <button
                      onClick={handleDeploySelected}
                      className="px-3 py-1 bg-primary text-primary-foreground font-semibold text-xs rounded hover:bg-primary/90 transition-colors"
                    >
                      Deploy Stack
                    </button>
                    <button
                      onClick={loadDashboardData}
                      className="p-1 rounded hover:bg-secondary text-muted-foreground hover:text-foreground"
                    >
                      <RefreshCw className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                <div className="divide-y divide-border text-xs">
                  {["searxng", "crawl4ai", "valkey", "egress-web"].map((s) => (
                    <div key={s} className="py-3 flex items-center justify-between">
                      <div className="flex items-center space-x-3">
                        <span className="h-2 w-2 rounded-full bg-status-ok"></span>
                        <span className="font-semibold uppercase">{s}</span>
                      </div>
                      <span className="text-[11px] text-muted-foreground">Hardened Non-Root Container</span>
                      <span className="text-[11px] text-status-ok border border-status-ok/30 px-2 py-0.5 rounded">
                        Healthy
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* ENGINE CATALOG VIEW */}
          {activeTab === "catalog" && (
            <div className="space-y-4">
              <div>
                <h2 className="text-base font-bold uppercase">Supported Search & Scraping Engines</h2>
                <p className="text-xs text-muted-foreground">
                  Each engine runs under strict seccomp profiles, non-root uid:gid, and fail-closed internal networks.
                </p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {catalog.map((item) => (
                  <div
                    key={item.id}
                    className="p-4 rounded border border-border bg-card/80 space-y-3 relative hover:border-primary/50 transition-colors"
                  >
                    <div className="flex items-center justify-between">
                      <h3 className="text-sm font-bold text-foreground uppercase">{item.name}</h3>
                      <span className="text-[10px] border border-border px-2 py-0.5 rounded uppercase text-muted-foreground">
                        {item.tier}
                      </span>
                    </div>

                    <div className="text-[11px] text-muted-foreground">
                      License: <span className="text-foreground">{item.license.spdx}</span>
                    </div>

                    {item.license.notice && (
                      <div className="text-[10px] p-2 rounded bg-secondary/50 text-status-warn border border-status-warn/20">
                        {item.license.notice}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* AGENT CONNECTIONS VIEW */}
          {activeTab === "agents" && (
            <div className="space-y-6">
              <div className="p-6 rounded border border-border bg-card space-y-4">
                <h2 className="text-sm font-bold uppercase tracking-wider">Generate Agent Key</h2>
                <p className="text-xs text-muted-foreground">
                  Creates an authenticated Bearer token and ready-to-use OpenCode v2 configuration block.
                </p>

                <div className="flex gap-3">
                  <input
                    type="text"
                    placeholder="Agent name (e.g. opencode-agent-1)"
                    value={newKeyName}
                    onChange={(e) => setNewKeyName(e.target.value)}
                    className="flex-1 bg-input border border-border rounded px-3 py-2 text-xs focus:outline-none focus:border-primary"
                  />
                  <button
                    onClick={handleCreateAgentKey}
                    className="px-4 py-2 bg-primary text-primary-foreground font-semibold text-xs rounded hover:bg-primary/90 transition-colors"
                  >
                    Generate Key
                  </button>
                </div>

                {createdSnippet && (
                  <div className="mt-4 space-y-2">
                    <div className="flex items-center justify-between text-xs text-muted-foreground">
                      <span>opencode.json configuration:</span>
                      <button
                        onClick={() => navigator.clipboard.writeText(createdSnippet)}
                        className="flex items-center gap-1 text-primary hover:underline"
                      >
                        <Copy className="h-3 w-3" /> Copy
                      </button>
                    </div>
                    <pre className="p-4 rounded bg-background border border-border text-[11px] overflow-x-auto text-status-info">
                      {createdSnippet}
                    </pre>
                  </div>
                )}
              </div>

              {/* Active Keys List */}
              <div className="p-6 rounded border border-border bg-card space-y-4">
                <h2 className="text-sm font-bold uppercase tracking-wider">Active Keys</h2>
                <div className="divide-y divide-border text-xs">
                  {agentKeys.map((k) => (
                    <div key={k.id} className="py-3 flex items-center justify-between">
                      <div>
                        <div className="font-semibold text-foreground">{k.name}</div>
                        <div className="text-[10px] text-muted-foreground">{k.key_prefix}</div>
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        Scopes: {k.scopes.join(", ")}
                      </div>
                    </div>
                  ))}
                  {agentKeys.length === 0 && (
                    <div className="py-4 text-center text-xs text-muted-foreground">
                      No agent keys generated yet.
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* INSTALL WIZARD STEPPER */}
          {activeTab === "wizard" && (
            <div className="max-w-xl mx-auto p-6 rounded border border-border bg-card space-y-6 blood-border">
              <div>
                <h2 className="text-base font-bold uppercase tracking-wider text-foreground">
                  Installation & Setup Wizard
                </h2>
                <p className="text-xs text-muted-foreground mt-1">
                  Initialize administrator credentials, enroll TOTP 2FA, and deploy core search/scraping engines.
                </p>
              </div>

              {!totpSecret ? (
                <div className="space-y-4">
                  <div>
                    <label className="text-xs text-muted-foreground uppercase block mb-1">
                      One-Time Bootstrap Token
                    </label>
                    <input
                      type="password"
                      placeholder="Paste bootstrap token from install output"
                      value={bootstrapToken}
                      onChange={(e) => setBootstrapToken(e.target.value)}
                      className="w-full bg-input border border-border rounded px-3 py-2 text-xs focus:outline-none focus:border-primary"
                    />
                  </div>

                  <div>
                    <label className="text-xs text-muted-foreground uppercase block mb-1">
                      Admin Username
                    </label>
                    <input
                      type="text"
                      value={adminUsername}
                      onChange={(e) => setAdminUsername(e.target.value)}
                      className="w-full bg-input border border-border rounded px-3 py-2 text-xs focus:outline-none focus:border-primary"
                    />
                  </div>

                  <button
                    onClick={handleBootstrapInit}
                    className="w-full py-2 bg-primary text-primary-foreground font-semibold text-xs rounded hover:bg-primary/90 transition-colors"
                  >
                    Proceed to 2FA Setup
                  </button>
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="p-3 rounded bg-secondary/40 border border-border text-xs space-y-2">
                    <span className="font-semibold block text-primary">TOTP Enrollment Key:</span>
                    <code className="text-sm font-bold tracking-widest text-status-info block">
                      {totpSecret}
                    </code>
                    {totpUri && (
                      <span className="text-[10px] text-muted-foreground block truncate">
                        URI: {totpUri}
                      </span>
                    )}
                    <span className="text-[10px] text-muted-foreground block">
                      Enter this secret into your authenticator app (Google Authenticator, Bitwarden, etc.)
                    </span>
                  </div>

                  <div>
                    <label className="text-xs text-muted-foreground uppercase block mb-1">
                      6-Digit Authenticator Code
                    </label>
                    <input
                      type="text"
                      maxLength={6}
                      placeholder="000000"
                      value={totpCode}
                      onChange={(e) => setTotpCode(e.target.value)}
                      className="w-full bg-input border border-border rounded px-3 py-2 text-center text-sm font-bold tracking-widest focus:outline-none focus:border-primary"
                    />
                  </div>

                  <button
                    onClick={handleVerifyTotp}
                    className="w-full py-2 bg-primary text-primary-foreground font-semibold text-xs rounded hover:bg-primary/90 transition-colors"
                  >
                    Verify & Finalize Stack
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
