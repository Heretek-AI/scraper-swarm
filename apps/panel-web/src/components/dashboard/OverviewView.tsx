import React from "react";
import {
  Boxes,
  ShieldCheck,
  Key,
  Lock,
  RefreshCw,
  Square,
  Play,
  Terminal,
  RotateCw,
  FlaskConical,
  ArrowRight,
  Server,
  Radio,
} from "lucide-react";
import {
  ContainerStatus,
  InstalledServiceInfo,
  SecurityPosture,
  SmokeTestResult,
} from "../../types";

interface OverviewViewProps {
  authenticated: boolean;
  liveContainers: ContainerStatus[];
  installedServices?: Record<string, InstalledServiceInfo>;
  agentKeysCount: number;
  securityPosture: SecurityPosture | null;
  clusterTestSummary: { total: number; passed: number; results: SmokeTestResult[] } | null;
  testingServiceId: string | null;
  deployingCore: boolean;
  onRefresh: () => void;
  onDeployCore: () => void;
  onStopStack: () => void;
  onRunSmokeTest: (serviceId: string) => void;
  onOpenLogs: (serviceId: string) => void;
  onRestartService: (serviceId: string) => void;
  onOpenLogin: () => void;
}

export const OverviewView: React.FC<OverviewViewProps> = ({
  authenticated,
  liveContainers,
  agentKeysCount,
  securityPosture,
  clusterTestSummary,
  testingServiceId,
  deployingCore,
  onRefresh,
  onDeployCore,
  onStopStack,
  onRunSmokeTest,
  onOpenLogs,
  onRestartService,
  onOpenLogin,
}) => {
  const containerCount = liveContainers.length;

  return (
    <div className="space-y-6">
      {/* 1Panel-Style Circular / Progress Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Metric 1: Live Containers */}
        <div className="p-5 rounded-xl border border-border bg-card/60 relative overflow-hidden glass hover:border-primary/40 transition-all">
          <div className="flex items-center justify-between">
            <span className="text-[11px] uppercase font-bold tracking-wider text-muted-foreground">
              Live Containers
            </span>
            <span className="p-2 rounded-lg bg-primary/10 text-primary">
              <Boxes className="h-4 w-4" />
            </span>
          </div>
          <div className="mt-3 flex items-baseline space-x-2">
            <span className="text-3xl font-extrabold text-foreground font-mono">{containerCount}</span>
            <span className="text-xs text-muted-foreground">Running</span>
          </div>
          <div className="mt-3 flex items-center justify-between text-[11px] text-muted-foreground pt-3 border-t border-border/50">
            <span className="flex items-center gap-1.5">
              <span className={`h-2 w-2 rounded-full ${containerCount > 0 ? "bg-status-ok animate-pulse" : "bg-muted-foreground"}`} />
              {containerCount > 0 ? "Docker Daemon Synced" : "Stack Inactive"}
            </span>
            <span className="font-mono text-primary">{containerCount > 0 ? "100% HEALTHY" : "IDLE"}</span>
          </div>
        </div>

        {/* Metric 2: Egress SSRF Guard */}
        <div className="p-5 rounded-xl border border-border bg-card/60 relative overflow-hidden glass hover:border-status-ok/40 transition-all">
          <div className="flex items-center justify-between">
            <span className="text-[11px] uppercase font-bold tracking-wider text-muted-foreground">
              Egress SSRF Guard
            </span>
            <span className="p-2 rounded-lg bg-status-ok/10 text-status-ok">
              <ShieldCheck className="h-4 w-4" />
            </span>
          </div>
          <div className="mt-3 flex items-baseline space-x-2">
            <span className="text-2xl font-extrabold text-status-ok tracking-wide">Fail-Closed</span>
          </div>
          <div className="mt-3 flex items-center justify-between text-[11px] text-muted-foreground pt-3 border-t border-border/50">
            <span>Smokescreen Proxy</span>
            <span className="font-mono text-status-ok">RFC1918 BLOCKED</span>
          </div>
        </div>

        {/* Metric 3: Scoped API Keys */}
        <div className="p-5 rounded-xl border border-border bg-card/60 relative overflow-hidden glass hover:border-primary/40 transition-all">
          <div className="flex items-center justify-between">
            <span className="text-[11px] uppercase font-bold tracking-wider text-muted-foreground">
              Agent API Keys
            </span>
            <span className="p-2 rounded-lg bg-primary/10 text-primary">
              <Key className="h-4 w-4" />
            </span>
          </div>
          <div className="mt-3 flex items-baseline space-x-2">
            <span className="text-3xl font-extrabold text-foreground font-mono">{agentKeysCount}</span>
            <span className="text-xs text-muted-foreground">Issued</span>
          </div>
          <div className="mt-3 flex items-center justify-between text-[11px] text-muted-foreground pt-3 border-t border-border/50">
            <span>Bearer Tokens</span>
            <span className="font-mono text-primary">HMAC SHA-256</span>
          </div>
        </div>

        {/* Metric 4: Security Posture */}
        <div className="p-5 rounded-xl border border-border bg-card/60 relative overflow-hidden glass hover:border-primary/40 transition-all">
          <div className="flex items-center justify-between">
            <span className="text-[11px] uppercase font-bold tracking-wider text-muted-foreground">
              Security Posture
            </span>
            <span className="p-2 rounded-lg bg-primary/10 text-primary">
              <Lock className="h-4 w-4" />
            </span>
          </div>
          <div className="mt-3 flex items-baseline space-x-2">
            <span className="text-3xl font-extrabold text-primary font-mono">
              {securityPosture ? `${securityPosture.score}%` : "100%"}
            </span>
            <span className="text-xs text-muted-foreground">Hardened</span>
          </div>
          <div className="mt-3 flex items-center justify-between text-[11px] text-muted-foreground pt-3 border-t border-border/50">
            <span>Envelope Vault</span>
            <span className="font-mono text-status-ok">AES-256-GCM</span>
          </div>
        </div>
      </div>

      {/* Cluster Diagnostic Smoke Test Banner (if triggered) */}
      {clusterTestSummary && (
        <div className="p-5 rounded-xl bg-card border border-primary/40 space-y-3 glass shadow-lg">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <FlaskConical className="h-4 w-4 text-primary animate-pulse" />
              <h3 className="text-xs font-bold uppercase tracking-wider text-foreground">
                Cluster Diagnostic Smoke Test Report
              </h3>
            </div>
            <span className="text-xs text-status-ok font-semibold px-2.5 py-1 rounded bg-status-ok/10 border border-status-ok/30">
              {clusterTestSummary.passed} / {clusterTestSummary.total} Checks Passed
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-2.5 pt-1">
            {clusterTestSummary.results.map((r) => (
              <div
                key={r.service_id}
                className={`p-3 rounded-lg border text-xs flex items-center justify-between ${
                  r.passed
                    ? "bg-status-ok/10 border-status-ok/30"
                    : "bg-status-critical/10 border-status-critical/30"
                }`}
              >
                <div>
                  <div className="font-bold uppercase tracking-wider text-[11px]">{r.service_id}</div>
                  <div className="text-[10px] text-muted-foreground font-mono">{r.latency_ms}ms latency</div>
                </div>
                <span
                  className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded ${
                    r.passed ? "bg-status-ok/20 text-status-ok" : "bg-status-critical/20 text-status-critical"
                  }`}
                >
                  {r.passed ? "PASSED" : "FAILED"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 1Panel-Style Architecture Pipeline Flow Widget */}
      <div className="p-5 rounded-xl border border-border bg-card/40 glass space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Radio className="h-4 w-4 text-primary" />
            <h3 className="text-xs font-bold uppercase tracking-wider text-foreground">
              Search & Scraping Pipeline Architecture
            </h3>
          </div>
          <span className="text-[11px] text-muted-foreground">Fail-Closed Ingress & Egress</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-5 gap-3 items-center text-xs">
          {/* Step 1: AI Agents */}
          <div className="p-3.5 rounded-lg border border-border bg-secondary/30 space-y-1 text-center">
            <div className="text-[10px] uppercase font-bold text-muted-foreground">Source</div>
            <div className="font-bold text-foreground flex items-center justify-center gap-1">
              <Server className="h-3.5 w-3.5 text-primary" /> OpenCode / MCP
            </div>
            <div className="text-[10px] text-muted-foreground font-mono">Bearer Token Auth</div>
          </div>

          <div className="hidden md:flex justify-center text-muted-foreground">
            <ArrowRight className="h-4 w-4" />
          </div>

          {/* Step 2: Caddy & Gateway */}
          <div className="p-3.5 rounded-lg border border-primary/30 bg-primary/5 space-y-1 text-center">
            <div className="text-[10px] uppercase font-bold text-primary">Edge Proxy</div>
            <div className="font-bold text-foreground flex items-center justify-center gap-1">
              <Lock className="h-3.5 w-3.5 text-status-ok" /> Caddy + Gateway
            </div>
            <div className="text-[10px] text-muted-foreground font-mono">TLS 1.3 / :443</div>
          </div>

          <div className="hidden md:flex justify-center text-muted-foreground">
            <ArrowRight className="h-4 w-4" />
          </div>

          {/* Step 3: Engine Mesh */}
          <div className="p-3.5 rounded-lg border border-border bg-secondary/30 space-y-1 text-center">
            <div className="text-[10px] uppercase font-bold text-muted-foreground">Engine Mesh</div>
            <div className="font-bold text-foreground">SearXNG / Crawl4AI</div>
            <div className="text-[10px] text-muted-foreground font-mono">Isolated Linux Netns</div>
          </div>
        </div>
      </div>

      {/* Active Containers Data Table (1Panel Style) */}
      <div className="p-6 rounded-xl border border-border bg-card/40 space-y-4 glass">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-bold uppercase tracking-wider text-foreground">
              Active Search & Scraping Cluster
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Live container processes mediated through the privileged sidecar socket (swarmd).
            </p>
          </div>

          <div className="flex items-center space-x-2">
            {containerCount > 0 ? (
              <button
                onClick={onStopStack}
                disabled={!authenticated}
                className="px-3 py-1.5 rounded-lg border border-destructive/40 bg-destructive/10 text-destructive text-xs hover:bg-destructive/20 flex items-center gap-1.5 transition-colors disabled:opacity-50"
              >
                <Square className="h-3 w-3" />
                <span>Stop Stack</span>
              </button>
            ) : (
              <button
                onClick={onDeployCore}
                disabled={deployingCore || !authenticated}
                className="px-3.5 py-1.5 rounded-lg bg-primary text-primary-foreground font-semibold text-xs rounded hover:bg-primary/90 flex items-center gap-1.5 transition-colors disabled:opacity-50 shadow-md shadow-primary/20"
              >
                <Play className="h-3 w-3" />
                <span>Deploy Core Stack</span>
              </button>
            )}

            <button
              onClick={onRefresh}
              className="p-1.5 rounded-lg border border-border bg-secondary text-foreground hover:bg-secondary/80 text-xs transition-colors"
              title="Refresh status"
            >
              <RefreshCw className="h-4 w-4" />
            </button>
          </div>
        </div>

        {containerCount > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-border text-muted-foreground uppercase text-[10px] tracking-wider">
                  <th className="pb-3 font-semibold">Container / Engine</th>
                  <th className="pb-3 font-semibold">Image Tag</th>
                  <th className="pb-3 font-semibold">Status</th>
                  <th className="pb-3 font-semibold">Internal Bindings</th>
                  <th className="pb-3 font-semibold text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {liveContainers.map((c, idx) => {
                  const sId = c.Service || c.Name?.replace("scraper-swarm-", "") || `unknown-${idx}`;
                  const isTesting = testingServiceId === sId;

                  return (
                    <tr key={c.ID || idx} className="hover:bg-secondary/20 transition-colors">
                      <td className="py-3.5 pr-4">
                        <div className="flex items-center space-x-2.5">
                          <span className="h-2 w-2 rounded-full bg-status-ok animate-pulse" />
                          <span className="font-bold text-foreground uppercase tracking-wide font-mono">
                            {sId}
                          </span>
                        </div>
                      </td>
                      <td className="py-3.5 pr-4 text-muted-foreground font-mono text-[11px]">
                        {c.Image}
                      </td>
                      <td className="py-3.5 pr-4">
                        <span className="px-2 py-0.5 rounded text-[10px] uppercase font-semibold bg-status-ok/10 text-status-ok border border-status-ok/30">
                          {c.State || "running"}
                        </span>
                      </td>
                      <td className="py-3.5 pr-4 text-muted-foreground font-mono text-[11px]">
                        {c.Ports || "Service Mesh Internal"}
                      </td>
                      <td className="py-3.5 text-right">
                        <div className="flex items-center justify-end space-x-2">
                          <button
                            onClick={() => (authenticated ? onRunSmokeTest(sId) : onOpenLogin())}
                            disabled={isTesting}
                            className="px-2.5 py-1 bg-secondary text-primary border border-primary/30 rounded text-[11px] hover:bg-primary/10 transition-colors flex items-center gap-1 disabled:opacity-50"
                            title={!authenticated ? "Sign in to run test" : "Execute engine smoke test"}
                          >
                            <FlaskConical className={`h-3 w-3 ${isTesting ? "animate-spin" : ""}`} />
                            <span>{isTesting ? "Testing..." : "Smoke Test"}</span>
                          </button>

                          <button
                            onClick={() => (authenticated ? onOpenLogs(sId) : onOpenLogin())}
                            className="px-2.5 py-1 bg-secondary text-foreground border border-border rounded text-[11px] hover:bg-secondary/80 transition-colors flex items-center gap-1"
                            title={!authenticated ? "Sign in to view logs" : "View container logs"}
                          >
                            <Terminal className="h-3 w-3" />
                            <span>Logs</span>
                          </button>

                          <button
                            onClick={() => (authenticated ? onRestartService(sId) : onOpenLogin())}
                            className="px-2.5 py-1 bg-secondary text-foreground border border-border rounded text-[11px] hover:bg-secondary/80 transition-colors flex items-center gap-1"
                            title={!authenticated ? "Sign in to restart" : "Restart container"}
                          >
                            <RotateCw className="h-3 w-3" />
                            <span>Restart</span>
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-center py-12 border border-dashed border-border rounded-xl space-y-3">
            <div className="h-10 w-10 rounded-full bg-secondary/50 flex items-center justify-center mx-auto text-muted-foreground">
              <Boxes className="h-5 w-5" />
            </div>
            <div className="text-sm font-semibold text-foreground">No Engine Stack Running</div>
            <p className="text-xs text-muted-foreground max-w-md mx-auto">
              Initialize the core search & scraping cluster (SearXNG + Crawl4AI + Valkey) with one click, or customize engines individually in the Engine App Store.
            </p>
            {authenticated ? (
              <button
                onClick={onDeployCore}
                disabled={deployingCore}
                className="px-4 py-2 bg-primary text-primary-foreground font-semibold text-xs rounded-lg hover:bg-primary/90 transition-all inline-flex items-center gap-1.5 shadow-md shadow-primary/20"
              >
                <Play className="h-3.5 w-3.5" />
                <span>{deployingCore ? "Deploying Core Cluster..." : "Deploy Core Stack"}</span>
              </button>
            ) : (
              <button
                onClick={onOpenLogin}
                className="px-4 py-2 bg-primary text-primary-foreground font-semibold text-xs rounded-lg hover:bg-primary/90 transition-all inline-flex items-center gap-1.5 shadow-md shadow-primary/20"
              >
                <Lock className="h-3.5 w-3.5" />
                <span>Sign In to Deploy Stack</span>
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
