import { useState } from "react";
import {
  X,
  Play,
  Lock,
  Cpu,
  Sliders,
  Key,
  HardDrive,
  FlaskConical,
  CheckCircle2,
  AlertTriangle,
  Shield,
  Eye,
  EyeOff,
} from "lucide-react";
import { ServiceItem, SmokeTestResult } from "../../types";

interface EngineConfigModalProps {
  service: ServiceItem;
  authenticated: boolean;
  activeProfile: string;
  initialParams: Record<string, any>;
  deploying: boolean;
  smokeTestResult?: SmokeTestResult;
  onClose: () => void;
  onDeploy: (serviceId: string, profile: string, params: Record<string, any>) => void;
  onRunSmokeTest: (serviceId: string) => void;
  onOpenLogin: () => void;
}

export const EngineConfigModal: React.FC<EngineConfigModalProps> = ({
  service,
  authenticated,
  activeProfile,
  initialParams,
  deploying,
  smokeTestResult,
  onClose,
  onDeploy,
  onRunSmokeTest,
  onOpenLogin,
}) => {
  const [configModalTab, setConfigModalTab] = useState<"resources" | "params" | "secrets" | "storage" | "diagnostics">("resources");
  const [selectedProfile, setSelectedProfile] = useState<string>(activeProfile || "standard");
  const [params, setParams] = useState<Record<string, any>>({ ...initialParams });
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [showSecret, setShowSecret] = useState<Record<string, boolean>>({});
  const [testing, setTesting] = useState(false);

  const toggleShowSecret = (key: string) => {
    setShowSecret((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const handleTestClick = async () => {
    setTesting(true);
    try {
      await onRunSmokeTest(service.id);
    } finally {
      setTesting(false);
    }
  };

  const profiles = ["lite", "standard", "heavy"] as const;

  return (
    <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4 backdrop-blur-sm animate-in fade-in">
      <div className="w-full max-w-2xl bg-card border border-primary/40 rounded-xl shadow-2xl flex flex-col max-h-[85vh] glass overflow-hidden">
        {/* Modal Header */}
        <div className="p-5 border-b border-border flex items-center justify-between bg-secondary/30">
          <div className="flex items-center space-x-3">
            <div className="h-9 w-9 rounded-lg bg-primary/10 border border-primary/40 flex items-center justify-center text-primary font-bold text-xs uppercase font-mono">
              {service.id.substring(0, 3)}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-bold uppercase tracking-wider text-foreground">
                  {authenticated ? `Configure ${service.name}` : `[Read-Only Spec] ${service.name}`}
                </h3>
                <span className="px-2 py-0.5 rounded text-[10px] uppercase font-semibold bg-secondary text-muted-foreground border border-border">
                  {service.tier}
                </span>
                {!authenticated && (
                  <span className="px-2 py-0.5 rounded text-[10px] uppercase font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/30 flex items-center gap-1">
                    <Lock className="h-3 w-3" /> Read-Only
                  </span>
                )}
              </div>
              <div className="text-[11px] text-muted-foreground mt-0.5 flex items-center gap-2">
                <span>License: <strong className="text-foreground">{service.license.spdx}</strong></span>
                <span>•</span>
                <span>Image: <code className="text-primary font-mono text-[10px]">{service.id}:latest</code></span>
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Read-Only Notice Banner for Unauthenticated Visitors */}
        {!authenticated && (
          <div className="p-3.5 bg-amber-500/10 border-b border-amber-500/30 text-xs text-amber-400 flex items-center justify-between px-5">
            <div className="flex items-center gap-2">
              <Lock className="h-4 w-4 shrink-0" />
              <span>
                <strong>Guest Mode:</strong> You are viewing engine specifications in read-only mode. Sign in to edit runtime parameters, manage secrets, and deploy.
              </span>
            </div>
            <button
              onClick={onOpenLogin}
              className="px-3 py-1 bg-amber-500 text-black font-semibold text-[11px] rounded-lg hover:bg-amber-400 transition-colors shrink-0 ml-3"
            >
              Sign In with TOTP
            </button>
          </div>
        )}

        {/* 5-Tab Segmented Navigation Bar */}
        <div className="flex items-center border-b border-border bg-secondary/15 px-5 overflow-x-auto text-xs font-semibold">
          <button
            onClick={() => setConfigModalTab("resources")}
            className={`py-3 px-3.5 border-b-2 flex items-center gap-1.5 transition-colors whitespace-nowrap ${
              configModalTab === "resources"
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            <Cpu className="h-3.5 w-3.5" />
            <span>1. Resource Profile</span>
          </button>

          <button
            onClick={() => setConfigModalTab("params")}
            className={`py-3 px-3.5 border-b-2 flex items-center gap-1.5 transition-colors whitespace-nowrap ${
              configModalTab === "params"
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            <Sliders className="h-3.5 w-3.5" />
            <span>2. Runtime Parameters</span>
          </button>

          <button
            onClick={() => setConfigModalTab("secrets")}
            className={`py-3 px-3.5 border-b-2 flex items-center gap-1.5 transition-colors whitespace-nowrap ${
              configModalTab === "secrets"
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            <Key className="h-3.5 w-3.5" />
            <span>3. Secrets & Vault</span>
          </button>

          <button
            onClick={() => setConfigModalTab("storage")}
            className={`py-3 px-3.5 border-b-2 flex items-center gap-1.5 transition-colors whitespace-nowrap ${
              configModalTab === "storage"
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            <HardDrive className="h-3.5 w-3.5" />
            <span>4. Storage & Sandbox</span>
          </button>

          <button
            onClick={() => setConfigModalTab("diagnostics")}
            className={`py-3 px-3.5 border-b-2 flex items-center gap-1.5 transition-colors whitespace-nowrap ${
              configModalTab === "diagnostics"
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            <FlaskConical className="h-3.5 w-3.5" />
            <span>5. Diagnostics</span>
          </button>
        </div>

        {/* Tab Body Content */}
        <div className="p-6 flex-1 overflow-y-auto space-y-5 text-xs">
          {/* TAB 1: RESOURCES */}
          {configModalTab === "resources" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-bold uppercase tracking-wider text-foreground">
                  Select Resource Profile
                </h4>
                <span className="text-[11px] text-muted-foreground">Hardened cgroups & memory limits</span>
              </div>

              <div className="grid grid-cols-3 gap-3">
                {profiles.map((p) => {
                  const res = service.resources?.[p];
                  const isSelected = selectedProfile === p;

                  return (
                    <button
                      key={p}
                      type="button"
                      disabled={!authenticated}
                      onClick={() => setSelectedProfile(p)}
                      className={`p-4 rounded-xl border text-left flex flex-col justify-between transition-all ${
                        isSelected
                          ? "border-primary bg-primary/10 shadow-md shadow-primary/10"
                          : "border-border bg-secondary/20 hover:border-primary/40"
                      } ${!authenticated ? "cursor-not-allowed opacity-75" : ""}`}
                    >
                      <div className="flex items-center justify-between w-full">
                        <span className="font-bold uppercase tracking-wider text-xs">{p}</span>
                        {isSelected && <span className="h-2 w-2 rounded-full bg-primary animate-pulse" />}
                      </div>
                      <div className="mt-3 space-y-1">
                        <div className="text-sm font-bold text-foreground font-mono">
                          {res?.mem || "Default"} RAM
                        </div>
                        <div className="text-[11px] text-muted-foreground font-mono">
                          {res?.cpus || 1} CPU Core(s)
                        </div>
                        {res?.shm && (
                          <div className="text-[10px] text-primary/80 font-mono">
                            SHM: {res.shm}
                          </div>
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>

              <div className="p-3.5 rounded-lg bg-secondary/30 border border-border text-muted-foreground space-y-1">
                <div className="font-semibold text-foreground flex items-center gap-1.5">
                  <Shield className="h-3.5 w-3.5 text-status-ok" /> Container Sandboxing Enforced
                </div>
                <p className="text-[11px] leading-relaxed">
                  All engine containers execute with strictly dropped Linux capabilities (<code>CAP_DROP: ALL</code>), unprivileged non-root UIDs, and <code>no-new-privileges: true</code>.
                </p>
              </div>
            </div>
          )}

          {/* TAB 2: PARAMETERS */}
          {configModalTab === "params" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-bold uppercase tracking-wider text-foreground">
                  Engine Runtime Parameters
                </h4>
                <span className="text-[11px] text-muted-foreground">Configured via environment injections</span>
              </div>

              {/* SearXNG Specific Parameters */}
              {service.id === "searxng" && (
                <div className="space-y-3.5">
                  <div>
                    <label className="text-muted-foreground block mb-1">Search Language</label>
                    <input
                      type="text"
                      disabled={!authenticated}
                      value={params.search_language || "en"}
                      onChange={(e) => setParams({ ...params, search_language: e.target.value })}
                      className="w-full bg-input border border-border rounded-lg px-3 py-2 text-xs font-mono disabled:opacity-60 disabled:cursor-not-allowed"
                      placeholder="e.g. en, de, fr, auto"
                    />
                  </div>
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="text-foreground font-medium block">Safe Search Filter</span>
                      <span className="text-[10px] text-muted-foreground">Filter adult / illicit search content</span>
                    </div>
                    <select
                      disabled={!authenticated}
                      value={params.safe_search ?? 0}
                      onChange={(e) => setParams({ ...params, safe_search: Number(e.target.value) })}
                      className="bg-input border border-border rounded-lg px-3 py-1.5 text-xs font-mono disabled:opacity-60 disabled:cursor-not-allowed"
                    >
                      <option value={0}>0 - None (Off)</option>
                      <option value={1}>1 - Moderate</option>
                      <option value={2}>2 - Strict</option>
                    </select>
                  </div>
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="text-foreground font-medium block">Autocomplete Engine</span>
                      <span className="text-[10px] text-muted-foreground">Typeahead suggestion source</span>
                    </div>
                    <select
                      disabled={!authenticated}
                      value={params.autocomplete || "google"}
                      onChange={(e) => setParams({ ...params, autocomplete: e.target.value })}
                      className="bg-input border border-border rounded-lg px-3 py-1.5 text-xs font-mono disabled:opacity-60 disabled:cursor-not-allowed"
                    >
                      <option value="google">Google</option>
                      <option value="duckduckgo">DuckDuckGo</option>
                      <option value="brave">Brave</option>
                      <option value="none">Disabled</option>
                    </select>
                  </div>
                </div>
              )}

              {/* Crawl4AI Specific Parameters */}
              {service.id === "crawl4ai" && (
                <div className="space-y-3.5">
                  <div>
                    <label className="text-muted-foreground block mb-1">LLM Provider (Optional)</label>
                    <input
                      type="text"
                      disabled={!authenticated}
                      placeholder="e.g. openai/gpt-4o-mini or groq/llama-3.2"
                      value={params.llm_provider || ""}
                      onChange={(e) => setParams({ ...params, llm_provider: e.target.value })}
                      className="w-full bg-input border border-border rounded-lg px-3 py-2 text-xs font-mono disabled:opacity-60 disabled:cursor-not-allowed"
                    />
                  </div>
                  <div>
                    <label className="text-muted-foreground block mb-1">LLM Base URL (For Local Ollama/vLLM)</label>
                    <input
                      type="text"
                      disabled={!authenticated}
                      placeholder="http://host.docker.internal:11434"
                      value={params.llm_base_url || ""}
                      onChange={(e) => setParams({ ...params, llm_base_url: e.target.value })}
                      className="w-full bg-input border border-border rounded-lg px-3 py-2 text-xs font-mono disabled:opacity-60 disabled:cursor-not-allowed"
                    />
                  </div>
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="text-foreground font-medium block">Headless Chromium</span>
                      <span className="text-[10px] text-muted-foreground">Run browser without display server</span>
                    </div>
                    <input
                      type="checkbox"
                      disabled={!authenticated}
                      checked={params.headless !== false}
                      onChange={(e) => setParams({ ...params, headless: e.target.checked })}
                      className="accent-primary h-4 w-4 disabled:opacity-60 disabled:cursor-not-allowed"
                    />
                  </div>
                  <div>
                    <label className="text-muted-foreground block mb-1">Page Timeout (seconds)</label>
                    <input
                      type="number"
                      disabled={!authenticated}
                      value={params.page_timeout || 30}
                      onChange={(e) => setParams({ ...params, page_timeout: Number(e.target.value) })}
                      className="w-full bg-input border border-border rounded-lg px-3 py-2 text-xs font-mono disabled:opacity-60 disabled:cursor-not-allowed"
                    />
                  </div>
                </div>
              )}

              {/* Scrapling Specific Parameters */}
              {service.id === "scrapling" && (
                <div className="space-y-3.5">
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="text-foreground font-medium block">Stealth Engine</span>
                      <span className="text-[10px] text-muted-foreground">Camoufox antidetect browser engine</span>
                    </div>
                    <select
                      disabled={!authenticated}
                      value={params.stealth_engine || "camoufox"}
                      onChange={(e) => setParams({ ...params, stealth_engine: e.target.value })}
                      className="bg-input border border-border rounded-lg px-3 py-1.5 text-xs font-mono disabled:opacity-60 disabled:cursor-not-allowed"
                    >
                      <option value="camoufox">Camoufox (Antidetect)</option>
                      <option value="playwright">Standard Playwright</option>
                    </select>
                  </div>
                  <div>
                    <label className="text-muted-foreground block mb-1">Network Timeout (seconds)</label>
                    <input
                      type="number"
                      disabled={!authenticated}
                      value={params.network_timeout || 30}
                      onChange={(e) => setParams({ ...params, network_timeout: Number(e.target.value) })}
                      className="w-full bg-input border border-border rounded-lg px-3 py-2 text-xs font-mono disabled:opacity-60 disabled:cursor-not-allowed"
                    />
                  </div>
                </div>
              )}

              {/* Default fallback for other engines */}
              {!["searxng", "crawl4ai", "scrapling"].includes(service.id) && (
                <div className="p-4 rounded-lg bg-secondary/30 border border-border text-muted-foreground">
                  Using default catalog manifest settings for {service.name}. Custom parameters are validated against strict schema before application.
                </div>
              )}
            </div>
          )}

          {/* TAB 3: SECRETS & VAULT */}
          {configModalTab === "secrets" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-bold uppercase tracking-wider text-foreground">
                  Cryptographic Secrets & Tokens
                </h4>
                <span className="text-[10px] text-status-ok font-mono">AES-256 ENVELOPE ENCRYPTED</span>
              </div>

              {!authenticated ? (
                /* Unauthenticated Vault Lockdown Screen */
                <div className="p-6 rounded-xl border border-border bg-secondary/20 text-center space-y-3">
                  <div className="h-10 w-10 rounded-full bg-primary/10 border border-primary/40 flex items-center justify-center text-primary mx-auto">
                    <Lock className="h-5 w-5" />
                  </div>
                  <div className="font-semibold text-foreground text-xs uppercase tracking-wide">
                    Vault Credentials Locked
                  </div>
                  <p className="text-[11px] text-muted-foreground max-w-sm mx-auto leading-relaxed">
                    Cryptographic tokens and API keys are stored in an encrypted envelope with strict 0600 permissions. Authenticate as an administrator to inject or rotate secrets.
                  </p>
                  <button
                    onClick={onOpenLogin}
                    className="px-3.5 py-1.5 bg-primary text-primary-foreground font-semibold text-xs rounded-lg hover:bg-primary/90 transition-colors"
                  >
                    Sign In to Unlock Vault
                  </button>
                </div>
              ) : (
                /* Authenticated Secrets Inputs */
                <div className="space-y-3.5">
                  {service.id === "crawl4ai" && (
                    <div className="space-y-3">
                      <div>
                        <label className="text-muted-foreground block mb-1">CRAWL4AI_API_TOKEN</label>
                        <div className="relative">
                          <input
                            type={showSecret["CRAWL4AI_API_TOKEN"] ? "text" : "password"}
                            placeholder="Auto-generated if left empty..."
                            value={secrets.CRAWL4AI_API_TOKEN || ""}
                            onChange={(e) => setSecrets({ ...secrets, CRAWL4AI_API_TOKEN: e.target.value })}
                            className="w-full bg-input border border-border rounded-lg pl-3 pr-9 py-2 text-xs font-mono"
                          />
                          <button
                            type="button"
                            onClick={() => toggleShowSecret("CRAWL4AI_API_TOKEN")}
                            className="absolute right-2.5 top-2.5 text-muted-foreground hover:text-foreground"
                          >
                            {showSecret["CRAWL4AI_API_TOKEN"] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                          </button>
                        </div>
                      </div>
                      <div>
                        <label className="text-muted-foreground block mb-1">OPENAI_API_KEY (Optional for Extraction)</label>
                        <div className="relative">
                          <input
                            type={showSecret["OPENAI_API_KEY"] ? "text" : "password"}
                            placeholder="sk-..."
                            value={secrets.OPENAI_API_KEY || ""}
                            onChange={(e) => setSecrets({ ...secrets, OPENAI_API_KEY: e.target.value })}
                            className="w-full bg-input border border-border rounded-lg pl-3 pr-9 py-2 text-xs font-mono"
                          />
                          <button
                            type="button"
                            onClick={() => toggleShowSecret("OPENAI_API_KEY")}
                            className="absolute right-2.5 top-2.5 text-muted-foreground hover:text-foreground"
                          >
                            {showSecret["OPENAI_API_KEY"] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                          </button>
                        </div>
                      </div>
                    </div>
                  )}

                  {service.id === "searxng" && (
                    <div className="p-3.5 rounded-lg bg-secondary/30 border border-border text-muted-foreground space-y-1">
                      <div className="font-semibold text-foreground">SEARXNG_SECRET</div>
                      <p className="text-[11px]">
                        Cryptographically secure 32-byte secret is automatically provisioned into <code>env/searxng.env</code> with strict 0600 permissions.
                      </p>
                    </div>
                  )}

                  {!["crawl4ai", "searxng"].includes(service.id) && (
                    <div className="p-3.5 rounded-lg bg-secondary/30 border border-border text-muted-foreground">
                      No custom credentials required for {service.name}. System secrets will be provisioned by swarmd automatically.
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* TAB 4: STORAGE & SANDBOXING */}
          {configModalTab === "storage" && (
            <div className="space-y-4">
              <h4 className="text-xs font-bold uppercase tracking-wider text-foreground">
                Container Volumes & Network Sandboxing
              </h4>

              <div className="space-y-2 font-mono text-[11px]">
                <div className="p-3 rounded-lg bg-secondary/30 border border-border flex items-center justify-between">
                  <span className="text-muted-foreground">Internal Network:</span>
                  <span className="text-foreground">swarm-svc (Isolated Bridge)</span>
                </div>
                <div className="p-3 rounded-lg bg-secondary/30 border border-border flex items-center justify-between">
                  <span className="text-muted-foreground">Egress Routing:</span>
                  <span className="text-status-ok font-semibold">Via Smokescreen Proxy (:4750)</span>
                </div>
                <div className="p-3 rounded-lg bg-secondary/30 border border-border flex items-center justify-between">
                  <span className="text-muted-foreground">Seccomp Profile:</span>
                  <span className="text-foreground">chromium.json (Dropped Syscalls)</span>
                </div>
                <div className="p-3 rounded-lg bg-secondary/30 border border-border flex items-center justify-between">
                  <span className="text-muted-foreground">Root Filesystem:</span>
                  <span className="text-foreground">Read-Only with Tempfs</span>
                </div>
              </div>
            </div>
          )}

          {/* TAB 5: DIAGNOSTICS */}
          {configModalTab === "diagnostics" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h4 className="text-xs font-bold uppercase tracking-wider text-foreground">
                    Automated Smoke Test Diagnostics
                  </h4>
                  <p className="text-[11px] text-muted-foreground">
                    Direct synthetic probe testing HTTP reachability and egress guard enforcement.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={handleTestClick}
                  disabled={testing || !authenticated}
                  className="px-3 py-1.5 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 transition-colors flex items-center gap-1.5 disabled:opacity-50"
                  title={!authenticated ? "Sign in required" : "Run diagnostics"}
                >
                  <FlaskConical className={`h-3.5 w-3.5 ${testing ? "animate-spin" : ""}`} />
                  <span>{testing ? "Testing..." : "Run Smoke Test"}</span>
                </button>
              </div>

              {smokeTestResult ? (
                <div className="space-y-3 pt-2">
                  <div
                    className={`p-3 rounded-lg border text-xs flex items-center justify-between ${
                      smokeTestResult.passed
                        ? "bg-status-ok/10 border-status-ok/30 text-status-ok"
                        : "bg-status-critical/10 border-status-critical/30 text-status-critical"
                    }`}
                  >
                    <span className="font-bold">
                      {smokeTestResult.passed ? "All Diagnostic Checks Passed" : "Diagnostics Encountered Errors"}
                    </span>
                    <span className="font-mono text-[11px]">
                      Latency: {smokeTestResult.latency_ms}ms
                    </span>
                  </div>

                  <div className="space-y-1.5">
                    {smokeTestResult.checks.map((c, i) => (
                      <div
                        key={i}
                        className="p-3 rounded-lg bg-secondary/40 border border-border text-xs flex items-center justify-between"
                      >
                        <span className="font-medium text-foreground">{c.name}</span>
                        <span className={`flex items-center gap-1 text-[11px] ${c.passed ? "text-status-ok font-semibold" : "text-status-critical font-semibold"}`}>
                          {c.passed ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
                          {c.message}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="text-center py-8 text-muted-foreground text-xs border border-dashed border-border rounded-xl">
                  Click "Run Smoke Test" to verify container health, HTTP response codes, and SSRF egress blocking.
                </div>
              )}
            </div>
          )}
        </div>

        {/* Modal Bottom Action Footer */}
        <div className="p-4 border-t border-border bg-secondary/20 flex items-center justify-between">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-secondary text-foreground text-xs rounded-lg hover:bg-secondary/80 border border-border transition-colors"
          >
            Close
          </button>

          {authenticated ? (
            <button
              onClick={() => onDeploy(service.id, selectedProfile, params)}
              disabled={deploying}
              className="px-5 py-2 bg-primary text-primary-foreground font-semibold text-xs rounded-lg hover:bg-primary/90 transition-all flex items-center gap-2 shadow-md shadow-primary/20 disabled:opacity-50"
            >
              <Play className="h-3.5 w-3.5" />
              <span>{deploying ? "Applying Configuration..." : "Save & Apply Configuration"}</span>
            </button>
          ) : (
            <button
              onClick={onOpenLogin}
              className="px-5 py-2 bg-primary text-primary-foreground font-semibold text-xs rounded-lg hover:bg-primary/90 transition-all flex items-center gap-2 shadow-md shadow-primary/20"
            >
              <Lock className="h-3.5 w-3.5" />
              <span>Sign In to Configure & Deploy</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
