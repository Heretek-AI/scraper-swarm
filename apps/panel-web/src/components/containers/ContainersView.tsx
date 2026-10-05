import { useState, useEffect } from "react";
import {
  Boxes,
  FileCode,
  Copy,
  Check,
  RefreshCw,
  Lock,
} from "lucide-react";
import { ContainerStatus, InstalledServiceInfo } from "../../types";
import { ContainersTable } from "./ContainersTable";

interface ContainersViewProps {
  authenticated: boolean;
  liveContainers: ContainerStatus[];
  installedServices?: Record<string, InstalledServiceInfo>;
  testingServiceId: string | null;
  onRefresh: () => void;
  onOpenLogs: (serviceId: string) => void;
  onRestartService: (serviceId: string) => void;
  onRunSmokeTest: (serviceId: string) => void;
  onOpenLogin: () => void;
}

export const ContainersView: React.FC<ContainersViewProps> = ({
  authenticated,
  liveContainers,
  testingServiceId,
  onRefresh,
  onOpenLogs,
  onRestartService,
  onRunSmokeTest,
  onOpenLogin,
}) => {
  const [activeSubTab, setActiveSubTab] = useState<"containers" | "compose">("containers");
  const [composeYaml, setComposeYaml] = useState<string>("");
  const [loadingCompose, setLoadingCompose] = useState<boolean>(false);
  const [copied, setCopied] = useState<boolean>(false);

  useEffect(() => {
    if (activeSubTab === "compose" && authenticated) {
      loadCompose();
    }
  }, [activeSubTab, authenticated]);

  async function loadCompose() {
    setLoadingCompose(true);
    try {
      const res = await fetch("/services/compose");
      if (res.ok) {
        const data = await res.json();
        setComposeYaml(data.compose_yaml || "# No active stack rendered");
      }
    } catch (e) {
      console.error("Failed to load compose", e);
    } finally {
      setLoadingCompose(false);
    }
  }

  function handleCopyCompose() {
    navigator.clipboard.writeText(composeYaml);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="space-y-6">
      {/* 1Panel-Style Containers & Stacks Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center space-x-2">
            <Boxes className="h-5 w-5 text-primary" />
            <h2 className="text-base font-bold uppercase tracking-wider text-foreground">
              Container Management & Compose Stacks
            </h2>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            Real-time inspection of isolated Linux containers, bridge networks, and declarative compose manifests.
          </p>
        </div>

        {/* Sub-tab Switcher: Containers vs Compose */}
        <div className="flex items-center space-x-1 p-1 rounded-lg bg-secondary/30 border border-border text-xs">
          <button
            onClick={() => setActiveSubTab("containers")}
            className={`px-3 py-1.5 rounded-md font-semibold transition-all ${
              activeSubTab === "containers"
                ? "bg-primary text-primary-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            Container Pods ({liveContainers.length})
          </button>
          <button
            onClick={() => setActiveSubTab("compose")}
            className={`px-3 py-1.5 rounded-md font-semibold transition-all ${
              activeSubTab === "compose"
                ? "bg-primary text-primary-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            docker-compose.yml
          </button>
        </div>
      </div>

      {/* Guest Mode Warning if unauthenticated */}
      {!authenticated && (
        <div className="p-3.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-xs text-amber-400 flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Lock className="h-4 w-4" />
            Container orchestration telemetry requires verified administrator identity.
          </span>
          <button
            onClick={onOpenLogin}
            className="px-3 py-1 bg-amber-500 text-black font-semibold text-[11px] rounded-lg hover:bg-amber-400 transition-colors"
          >
            Sign In with TOTP
          </button>
        </div>
      )}

      {/* SUB-TAB 1: LIVE CONTAINER PODS TABLE */}
      {activeSubTab === "containers" && (
        <div className="p-6 rounded-xl border border-border bg-card/40 glass space-y-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div className="text-xs font-bold uppercase tracking-wider text-foreground">
              Running Pod Execution Details
            </div>
            <button
              onClick={onRefresh}
              className="p-1.5 rounded-lg border border-border bg-secondary text-foreground hover:bg-secondary/80 text-xs transition-colors flex items-center gap-1.5"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              <span>Refresh Pods</span>
            </button>
          </div>

          <ContainersTable
            containers={liveContainers}
            testingServiceId={testingServiceId}
            authenticated={authenticated}
            showContainerId={true}
            onRunSmokeTest={onRunSmokeTest}
            onOpenLogs={onOpenLogs}
            onRestartService={onRestartService}
            onOpenLogin={onOpenLogin}
          />
        </div>
      )}

      {/* SUB-TAB 2: DOCKER COMPOSE STACK INSPECTOR */}
      {activeSubTab === "compose" && (
        <div className="p-6 rounded-xl border border-border bg-card/40 glass space-y-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <FileCode className="h-4 w-4 text-primary" />
              <h3 className="text-xs font-bold uppercase tracking-wider text-foreground">
                Rendered docker-compose.yml Manifest
              </h3>
            </div>
            <div className="flex items-center space-x-2">
              <button
                onClick={handleCopyCompose}
                disabled={!composeYaml}
                className="px-3 py-1.5 rounded-lg border border-border bg-secondary text-foreground hover:bg-secondary/80 text-xs flex items-center gap-1.5 transition-colors disabled:opacity-50"
              >
                {copied ? <Check className="h-3.5 w-3.5 text-status-ok" /> : <Copy className="h-3.5 w-3.5" />}
                <span>{copied ? "Copied!" : "Copy YAML"}</span>
              </button>
              <button
                onClick={loadCompose}
                disabled={loadingCompose}
                className="p-1.5 rounded-lg border border-border bg-secondary text-foreground hover:bg-secondary/80 text-xs transition-colors"
                title="Refresh YAML"
              >
                <RefreshCw className={`h-4 w-4 ${loadingCompose ? "animate-spin text-primary" : ""}`} />
              </button>
            </div>
          </div>

          <div className="p-4 bg-background/90 border border-border rounded-xl font-mono text-[11px] text-foreground leading-relaxed overflow-x-auto max-h-[60vh] whitespace-pre select-all">
            {loadingCompose ? (
              <span className="text-muted-foreground">Reading active compose manifest from swarmd...</span>
            ) : composeYaml ? (
              composeYaml
            ) : (
              <span className="text-muted-foreground">No active compose manifest found. Deploy services to generate.</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
