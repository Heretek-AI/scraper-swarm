import { useState, useEffect } from "react";
import { Sidebar, NavTab } from "./components/layout/Sidebar";
import { Header } from "./components/layout/Header";
import { OverviewView } from "./components/dashboard/OverviewView";
import { EngineStoreView } from "./components/appstore/EngineStoreView";
import { EngineConfigModal } from "./components/appstore/EngineConfigModal";
import { ContainersView } from "./components/containers/ContainersView";
import { WorkbenchView } from "./components/workbench/WorkbenchView";
import { SecurityView } from "./components/security/SecurityView";
import { WizardView } from "./components/wizard/WizardView";
import { LoginModal } from "./components/modals/LoginModal";
import { LogViewerModal } from "./components/modals/LogViewerModal";
import { ToastProvider, useToast } from "./components/ui/ToastContext";
import {
  ServiceItem,
  ContainerStatus,
  InstalledServiceInfo,
  SmokeTestResult,
  SecurityPosture,
  AuditEntry,
  AgentKey,
} from "./types";

function MainPanel() {
  const toast = useToast();

  // Navigation & Lifecycle states
  const [activeTab, setActiveTab] = useState<NavTab>("dashboard");
  const [setupCompleted, setSetupCompleted] = useState<boolean | null>(null);
  const [authenticated, setAuthenticated] = useState<boolean>(false);
  const [adminUsername, setAdminUsername] = useState<string>("admin");

  // Cluster & Catalog states
  const [catalog, setCatalog] = useState<ServiceItem[]>([]);
  const [liveContainers, setLiveContainers] = useState<ContainerStatus[]>([]);
  const [installedServices, setInstalledServices] = useState<Record<string, InstalledServiceInfo>>({});
  const [agentKeys, setAgentKeys] = useState<AgentKey[]>([]);
  const [securityPosture, setSecurityPosture] = useState<SecurityPosture | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditEntry[]>([]);

  // Action states
  const [deployingCore, setDeployingCore] = useState<boolean>(false);
  const [deployingService, setDeployingService] = useState<boolean>(false);
  const [testingServiceId, setTestingServiceId] = useState<string | null>(null);
  const [smokeTestResults, setSmokeTestResults] = useState<Record<string, SmokeTestResult>>({});
  const [clusterTestRunning, setClusterTestRunning] = useState<boolean>(false);
  const [clusterTestSummary, setClusterTestSummary] = useState<{ total: number; passed: number; results: SmokeTestResult[] } | null>(null);

  // Modals
  const [selectedServiceForConfig, setSelectedServiceForConfig] = useState<ServiceItem | null>(null);
  const [activeLogService, setActiveLogService] = useState<string | null>(null);
  const [showLoginModal, setShowLoginModal] = useState<boolean>(false);

  useEffect(() => {
    checkInitialState();
  }, []);

  async function checkInitialState() {
    try {
      const statusRes = await fetch("/auth/status");
      const statusData = await statusRes.json();
      setSetupCompleted(statusData.setup_completed);

      // Check active session
      const meRes = await fetch("/auth/me");
      if (meRes.ok) {
        const u = await meRes.json();
        setAuthenticated(true);
        setAdminUsername(u.username || "admin");
        loadClusterData(true);
      } else {
        setAuthenticated(false);
        loadClusterData(false);
        if (!statusData.setup_completed) {
          setActiveTab("wizard");
        }
      }
    } catch (e) {
      console.error("Initialization check failed", e);
    }
  }

  async function loadClusterData(isAuth: boolean) {
    try {
      // Catalog is public
      const catRes = await fetch("/services/catalog");
      if (catRes.ok) setCatalog(await catRes.json());

      // Protected endpoints
      if (isAuth) {
        const statusRes = await fetch("/services/status");
        if (statusRes.ok) {
          const sData = await statusRes.json();
          setLiveContainers(sData.containers || []);
          setInstalledServices(sData.installed || {});
        }

        const keysRes = await fetch("/agents/keys");
        if (keysRes.ok) setAgentKeys(await keysRes.json());

        const postureRes = await fetch("/security/posture");
        if (postureRes.ok) setSecurityPosture(await postureRes.json());

        const auditRes = await fetch("/security/audit?limit=25");
        if (auditRes.ok) setAuditLogs(await auditRes.json());
      } else {
        setLiveContainers([]);
        setInstalledServices({});
        setAgentKeys([]);
      }
    } catch (e) {
      console.error("Error loading cluster data", e);
    }
  }

  // Auth Handlers
  async function handleLogin(username: string, code: string): Promise<boolean> {
    const res = await fetch("/auth/verify-totp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, code }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Authentication failed");

    setAuthenticated(true);
    setAdminUsername(username);
    setShowLoginModal(false);
    toast.success(`Welcome back, ${username}. Control deck unlocked.`);
    loadClusterData(true);
    return true;
  }

  async function handleLogout() {
    try {
      await fetch("/auth/logout", { method: "POST" });
      setAuthenticated(false);
      toast.info("You have signed out.");
      loadClusterData(false);
    } catch (e: any) {
      console.error("Logout error", e);
    }
  }

  // Wizard Handlers
  async function handleBootstrapInit(token: string, admin_username: string) {
    const res = await fetch("/auth/bootstrap-init", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, admin_username }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Bootstrap init failed");
    return { totpSecret: data.totp_secret, totpUri: data.totp_uri };
  }

  async function handleWizardVerify(username: string, code: string) {
    const ok = await handleLogin(username, code);
    if (ok) {
      setSetupCompleted(true);
      setActiveTab("dashboard");
    }
    return ok;
  }

  // Engine Actions
  async function handleDeployService(serviceId: string, profile: string, params: Record<string, any>) {
    setDeployingService(true);
    try {
      const payload = [{ service_id: serviceId, profile, params }];
      const res = await fetch("/services/deploy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Deployment failed");

      toast.success(`Service ${serviceId} deployed successfully.`);
      setSelectedServiceForConfig(null);
      loadClusterData(true);
    } catch (e: any) {
      toast.error(`Deploy error: ${e.message}`);
    } finally {
      setDeployingService(false);
    }
  }

  async function handleDeployCoreStack() {
    setDeployingCore(true);
    toast.info("Deploying core cluster (SearXNG + Crawl4AI + Valkey)...");
    try {
      const payload = [
        { service_id: "searxng", profile: "standard", params: {} },
        { service_id: "crawl4ai", profile: "standard", params: {} },
      ];
      const res = await fetch("/services/deploy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Stack deployment failed");

      toast.success("Core search & extraction cluster applied and running.");
      loadClusterData(true);
    } catch (e: any) {
      toast.error(`Stack deployment error: ${e.message}`);
    } finally {
      setDeployingCore(false);
    }
  }

  async function handleStopStack() {
    toast.warning("Tearing down engine containers via swarmd...");
    try {
      const res = await fetch("/services/down", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Stop failed");

      toast.info("Engine cluster stack stopped.");
      loadClusterData(true);
    } catch (e: any) {
      toast.error(`Stop error: ${e.message}`);
    }
  }

  async function handleRestartService(serviceId: string) {
    try {
      const res = await fetch(`/services/${serviceId}/restart`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Restart failed");

      toast.success(`Container ${serviceId} restarted.`);
      loadClusterData(true);
    } catch (e: any) {
      toast.error(`Restart failed: ${e.message}`);
    }
  }

  async function handleRunSmokeTest(serviceId: string) {
    setTestingServiceId(serviceId);
    try {
      const res = await fetch(`/services/${serviceId}/test`, { method: "POST" });
      const data: SmokeTestResult = await res.json();
      setSmokeTestResults((prev) => ({ ...prev, [serviceId]: data }));
      if (data.passed) {
        toast.success(`Smoke test passed for ${serviceId} (${data.latency_ms}ms).`);
      } else {
        toast.error(`Smoke test failed for ${serviceId}.`);
      }
    } catch (e: any) {
      toast.error(`Smoke test request error: ${e.message}`);
    } finally {
      setTestingServiceId(null);
    }
  }

  async function handleRunClusterDiagnostics() {
    setClusterTestRunning(true);
    setClusterTestSummary(null);
    toast.info("Executing synthetic diagnostics across cluster...");
    try {
      const res = await fetch("/services/test-all", { method: "POST" });
      const data = await res.json();
      setClusterTestSummary(data);
      if (data.results) {
        const mapped: Record<string, SmokeTestResult> = {};
        for (const r of data.results) {
          mapped[r.service_id] = r;
        }
        setSmokeTestResults((prev) => ({ ...prev, ...mapped }));
      }
      toast.success(`Cluster diagnostics complete: ${data.passed}/${data.total} passed.`);
    } catch (e: any) {
      toast.error(`Diagnostics error: ${e.message}`);
    } finally {
      setClusterTestRunning(false);
    }
  }

  // Agent Key Handlers
  async function handleCreateAgentKey(name: string, scopes: string[], rate_limit_rpm: number) {
    try {
      const res = await fetch("/agents/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, scopes, rate_limit_rpm }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Key creation failed");
      toast.success(`Bearer key for ${name} created.`);
      loadClusterData(true);
      return data;
    } catch (e: any) {
      toast.error(e.message);
      return null;
    }
  }

  async function handleRevokeAgentKey(keyId: string) {
    try {
      const res = await fetch(`/agents/keys/${keyId}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Revocation failed");
      toast.info("Agent key revoked.");
      loadClusterData(true);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  // Security Handlers
  async function handleVerifyAuditChain() {
    try {
      const res = await fetch("/security/verify-chain", { method: "POST" });
      const data = await res.json();
      if (data.valid) {
        toast.success(`Hash chain valid: ${data.entries_checked} blocks intact.`);
      } else {
        toast.error("Audit log tampering detected!");
      }
      return data;
    } catch (e: any) {
      toast.error(`Verification error: ${e.message}`);
      return null;
    }
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground font-mono">
      {/* 1Panel-Style Sidebar */}
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        authenticated={authenticated}
        username={adminUsername}
        liveContainers={liveContainers}
        securityPosture={securityPosture}
        agentKeysCount={agentKeys.length}
        setupCompleted={setupCompleted}
        onOpenLogin={() => setShowLoginModal(true)}
        onLogout={handleLogout}
      />

      {/* Main Dynamic Viewport */}
      <div className="flex-1 flex flex-col h-full overflow-hidden">
        {/* Top Header */}
        <Header
          activeTab={activeTab}
          authenticated={authenticated}
          username={adminUsername}
          setupCompleted={setupCompleted}
          liveContainers={liveContainers}
          securityPosture={securityPosture}
          clusterTestRunning={clusterTestRunning}
          deployingCore={deployingCore}
          onRunClusterTest={handleRunClusterDiagnostics}
          onDeployCore={handleDeployCoreStack}
          onOpenLogin={() => setShowLoginModal(true)}
          onLogout={handleLogout}
        />

        {/* View Router */}
        <main className="flex-1 overflow-y-auto p-6 space-y-6">
          {activeTab === "dashboard" && (
            <OverviewView
              authenticated={authenticated}
              liveContainers={liveContainers}
              installedServices={installedServices}
              agentKeysCount={agentKeys.length}
              securityPosture={securityPosture}
              clusterTestSummary={clusterTestSummary}
              testingServiceId={testingServiceId}
              deployingCore={deployingCore}
              onRefresh={() => loadClusterData(authenticated)}
              onDeployCore={handleDeployCoreStack}
              onStopStack={handleStopStack}
              onRunSmokeTest={handleRunSmokeTest}
              onOpenLogs={(sid) => setActiveLogService(sid)}
              onRestartService={handleRestartService}
              onOpenLogin={() => setShowLoginModal(true)}
            />
          )}

          {activeTab === "catalog" && (
            <EngineStoreView
              catalog={catalog}
              liveContainers={liveContainers}
              installedServices={installedServices}
              authenticated={authenticated}
              onOpenConfig={(item) => setSelectedServiceForConfig(item)}
              onOpenLogs={(sid) => setActiveLogService(sid)}
              onOpenLogin={() => setShowLoginModal(true)}
            />
          )}

          {activeTab === "containers" && (
            <ContainersView
              authenticated={authenticated}
              liveContainers={liveContainers}
              installedServices={installedServices}
              testingServiceId={testingServiceId}
              onRefresh={() => loadClusterData(authenticated)}
              onOpenLogs={(sid) => setActiveLogService(sid)}
              onRestartService={handleRestartService}
              onRunSmokeTest={handleRunSmokeTest}
              onOpenLogin={() => setShowLoginModal(true)}
            />
          )}

          {activeTab === "agents" && (
            <WorkbenchView
              authenticated={authenticated}
              agentKeys={agentKeys}
              onOpenLogin={() => setShowLoginModal(true)}
              onCreateKey={handleCreateAgentKey}
              onRevokeKey={handleRevokeAgentKey}
            />
          )}

          {activeTab === "security" && (
            <SecurityView
              authenticated={authenticated}
              securityPosture={securityPosture}
              auditLogs={auditLogs}
              onOpenLogin={() => setShowLoginModal(true)}
              onVerifyChain={handleVerifyAuditChain}
            />
          )}

          {activeTab === "wizard" && (
            <WizardView
              setupCompleted={setupCompleted}
              onBootstrapInit={handleBootstrapInit}
              onVerifyTotp={handleWizardVerify}
            />
          )}
        </main>
      </div>

      {/* 5-Tab 1Panel Engine Configuration Drawer */}
      {selectedServiceForConfig && (
        <EngineConfigModal
          service={selectedServiceForConfig}
          authenticated={authenticated}
          activeProfile={installedServices[selectedServiceForConfig.id]?.profile || "standard"}
          initialParams={installedServices[selectedServiceForConfig.id]?.params || {}}
          deploying={deployingService}
          smokeTestResult={smokeTestResults[selectedServiceForConfig.id]}
          onClose={() => setSelectedServiceForConfig(null)}
          onDeploy={handleDeployService}
          onRunSmokeTest={handleRunSmokeTest}
          onOpenLogin={() => setShowLoginModal(true)}
        />
      )}

      {/* Container Log Viewer Modal */}
      {activeLogService && (
        <LogViewerModal
          serviceId={activeLogService}
          onClose={() => setActiveLogService(null)}
        />
      )}

      {/* TOTP Login Modal */}
      {showLoginModal && (
        <LoginModal
          onClose={() => setShowLoginModal(false)}
          onLogin={handleLogin}
        />
      )}
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <MainPanel />
    </ToastProvider>
  );
}
