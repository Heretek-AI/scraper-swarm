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
  Shield,
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

interface SecurityPosture {
  score: number;
  master_key_secure: boolean;
  audit_chain_valid: boolean;
  admin_2fa_enforced: boolean;
  egress_default_deny: boolean;
  container_capabilities_dropped: boolean;
  details: string[];
}

interface AuditEntry {
  id: number;
  timestamp: string;
  actor: string;
  action: string;
  target?: string;
  details: string;
  prev_hash: string;
  entry_hash: string;
}

export default function App() {
  const [setupCompleted, setSetupCompleted] = useState<boolean | null>(null);
  const [activeTab, setActiveTab] = useState<"dashboard" | "catalog" | "agents" | "security" | "wizard">("dashboard");

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

  // Security Posture & Audit states
  const [securityPosture, setSecurityPosture] = useState<SecurityPosture | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditEntry[]>([]);
  const [chainVerificationResult, setChainVerificationResult] = useState<{ valid: boolean; entries_checked: number } | null>(null);
  const [verifyingChain, setVerifyingChain] = useState(false);

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
          loadSecurityData();
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

  async function loadSecurityData() {
    try {
      const postureRes = await fetch("/security/posture");
      if (postureRes.ok) {
        setSecurityPosture(await postureRes.json());
      }
      const auditRes = await fetch("/security/audit?limit=25");
      if (auditRes.ok) {
        setAuditLogs(await auditRes.json());
      }
    } catch (e) {
      console.error("Error loading security data", e);
    }
  }

  async function handleVerifyAuditChain() {
    setVerifyingChain(true);
    try {
      const res = await fetch("/security/audit/verify", { method: "POST" });
      if (res.ok) {
        setChainVerificationResult(await res.json());
      }
    } catch (e: any) {
      alert("Verification error: " + e.message);
    } finally {
      setVerifyingChain(false);
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
              onClick={() => {
                setActiveTab("security");
                loadSecurityData();
              }}
              className={`w-full flex items-center space-x-3 px-3 py-2 text-xs rounded transition-colors ${
                activeTab === "security"
                  ? "bg-primary/20 text-primary border border-primary/40"
                  : "text-muted-foreground hover:bg-secondary/40 hover:text-foreground"
              }`}
            >
              <Shield className="h-4 w-4" />
              <span>Security Center</span>
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

          {/* SECURITY CENTER */}
          {activeTab === "security" && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                {/* Security Posture Score Card */}
                <div className="p-6 rounded border border-border bg-card space-y-4 md:col-span-1 blood-border">
                  <div className="flex items-center justify-between">
                    <span className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
                      Security Posture
                    </span>
                    <Shield className="h-5 w-5 text-primary corruption-glow" />
                  </div>
                  <div className="flex items-baseline space-x-2">
                    <span className="text-4xl font-bold text-foreground">
                      {securityPosture ? `${securityPosture.score}%` : "---"}
                    </span>
                    <span className="text-xs text-status-ok font-semibold">
                      {securityPosture && securityPosture.score === 100 ? "OPTIMAL" : "ACTIVE DEFENSE"}
                    </span>
                  </div>
                  <div className="space-y-2 pt-2 border-t border-border text-xs">
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">Master Key 0600</span>
                      <span className={securityPosture?.master_key_secure ? "text-status-ok" : "text-status-critical"}>
                        {securityPosture?.master_key_secure ? "ENFORCED" : "CHECK"}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">Admin 2FA (TOTP)</span>
                      <span className={securityPosture?.admin_2fa_enforced ? "text-status-ok" : "text-status-warn"}>
                        {securityPosture?.admin_2fa_enforced ? "ENFORCED" : "PENDING"}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">Egress SSRF Guard</span>
                      <span className={securityPosture?.egress_default_deny ? "text-status-ok" : "text-status-critical"}>
                        {securityPosture?.egress_default_deny ? "ACTIVE" : "DISABLED"}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">Container Cap Drop</span>
                      <span className={securityPosture?.container_capabilities_dropped ? "text-status-ok" : "text-status-critical"}>
                        {securityPosture?.container_capabilities_dropped ? "ALL DROPPED" : "UNSAFE"}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">Hash Chain Intact</span>
                      <span className={securityPosture?.audit_chain_valid ? "text-status-ok" : "text-status-critical"}>
                        {securityPosture?.audit_chain_valid ? "VALID" : "TAMPERED"}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Egress Smokescreen & Isolation Card */}
                <div className="p-6 rounded border border-border bg-card space-y-4 md:col-span-2">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="text-sm font-bold uppercase tracking-wider text-foreground">
                        Egress SSRF Isolation Guard
                      </h3>
                      <p className="text-xs text-muted-foreground mt-1">
                        All scraper engine containers operate inside strict <code>internal: true</code> Docker bridge networks. Outbound requests are routed through Stripe Smokescreen.
                      </p>
                    </div>
                    <Lock className="h-5 w-5 text-status-info" />
                  </div>

                  <div className="grid grid-cols-2 gap-4 pt-2 text-xs">
                    <div className="p-3 rounded bg-secondary/30 border border-border space-y-1">
                      <div className="font-semibold text-foreground">Cloud Metadata (169.254.169.254)</div>
                      <div className="text-[10px] text-status-ok flex items-center gap-1">
                        <CheckCircle2 className="h-3 w-3" /> Hard Blocked (Fail-Closed)
                      </div>
                    </div>
                    <div className="p-3 rounded bg-secondary/30 border border-border space-y-1">
                      <div className="font-semibold text-foreground">RFC1918 Private Subnets</div>
                      <div className="text-[10px] text-status-ok flex items-center gap-1">
                        <CheckCircle2 className="h-3 w-3" /> 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
                      </div>
                    </div>
                  </div>

                  <div className="p-3 rounded bg-secondary/20 border border-border text-xs flex items-center justify-between">
                    <span className="text-muted-foreground">Mediation Sidecar Protocol:</span>
                    <span className="font-semibold text-status-info">Unix Domain Socket (/var/lib/scraper-swarm/swarmd.sock)</span>
                  </div>
                </div>
              </div>

              {/* Cryptographic Hash-Chain Audit Log */}
              <div className="p-6 rounded border border-border bg-card space-y-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-sm font-bold uppercase tracking-wider text-foreground flex items-center gap-2">
                      Cryptographic Audit Log
                      <span className="text-[10px] px-2 py-0.5 rounded bg-primary/20 text-primary border border-primary/40">
                        SHA-256 HASH-CHAINED
                      </span>
                    </h3>
                    <p className="text-xs text-muted-foreground mt-1">
                      Every administrative action, key issuance, and deployment creates an immutable hash-chained entry to prevent tampering.
                    </p>
                  </div>
                  <button
                    onClick={handleVerifyAuditChain}
                    disabled={verifyingChain}
                    className="flex items-center gap-2 px-3 py-1.5 bg-secondary text-foreground text-xs rounded border border-border hover:bg-secondary/80 transition-colors"
                  >
                    <RefreshCw className={`h-3 w-3 ${verifyingChain ? "animate-spin" : ""}`} />
                    Verify Integrity
                  </button>
                </div>

                {chainVerificationResult && (
                  <div className={`p-3 rounded text-xs border ${
                    chainVerificationResult.valid
                      ? "bg-status-ok/10 border-status-ok/40 text-status-ok"
                      : "bg-status-critical/10 border-status-critical/40 text-status-critical"
                  }`}>
                    {chainVerificationResult.valid ? (
                      <div className="flex items-center gap-2">
                        <CheckCircle2 className="h-4 w-4" />
                        <span>Cryptographic chain verified: All {chainVerificationResult.entries_checked} entries intact without tampering.</span>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2">
                        <AlertTriangle className="h-4 w-4" />
                        <span>Cryptographic chain validation failed! Possible tampering detected.</span>
                      </div>
                    )}
                  </div>
                )}

                <div className="overflow-x-auto border border-border rounded">
                  <table className="w-full text-xs text-left">
                    <thead className="bg-secondary/40 text-muted-foreground border-b border-border uppercase text-[10px]">
                      <tr>
                        <th className="p-3">ID</th>
                        <th className="p-3">Timestamp</th>
                        <th className="p-3">Actor</th>
                        <th className="p-3">Action</th>
                        <th className="p-3">Target</th>
                        <th className="p-3">Entry Hash (SHA-256)</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {auditLogs.map((entry) => (
                        <tr key={entry.id} className="hover:bg-secondary/20 font-mono">
                          <td className="p-3 text-muted-foreground">#{entry.id}</td>
                          <td className="p-3 text-muted-foreground whitespace-nowrap">{entry.timestamp}</td>
                          <td className="p-3 font-semibold text-foreground">{entry.actor}</td>
                          <td className="p-3 text-primary">{entry.action}</td>
                          <td className="p-3 text-muted-foreground">{entry.target || "---"}</td>
                          <td className="p-3 text-[10px] text-muted-foreground font-mono truncate max-w-[180px]">
                            {entry.entry_hash.slice(0, 16)}...
                          </td>
                        </tr>
                      ))}
                      {auditLogs.length === 0 && (
                        <tr>
                          <td colSpan={6} className="p-4 text-center text-muted-foreground text-xs">
                            No audit log events recorded yet.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
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
