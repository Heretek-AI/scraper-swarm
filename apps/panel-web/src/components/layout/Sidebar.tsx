import React from "react";
import {
  LayoutDashboard,
  Layers,
  Boxes,
  Cpu,
  ShieldCheck,
  Terminal,
  LogIn,
  LogOut,
  Shield,
  Activity,
} from "lucide-react";
import { ContainerStatus, SecurityPosture } from "../../types";

export type NavTab = "dashboard" | "catalog" | "containers" | "agents" | "security" | "wizard";

interface SidebarProps {
  activeTab: NavTab;
  setActiveTab: (tab: NavTab) => void;
  authenticated: boolean;
  username: string;
  liveContainers: ContainerStatus[];
  securityPosture: SecurityPosture | null;
  agentKeysCount: number;
  setupCompleted: boolean | null;
  onOpenLogin: () => void;
  onLogout: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  setActiveTab,
  authenticated,
  username,
  liveContainers,
  securityPosture,
  agentKeysCount,
  setupCompleted,
  onOpenLogin,
  onLogout,
}) => {
  const activeCount = liveContainers.length;

  return (
    <aside className="w-64 border-r border-border bg-card/95 flex flex-col justify-between p-4 glass select-none">
      <div className="space-y-6">
        {/* Brand & Cluster Status Header */}
        <div className="flex items-center space-x-3 px-2">
          <div className="h-9 w-9 rounded-lg border border-primary/50 bg-primary/10 flex items-center justify-center text-primary corruption-glow shadow-inner">
            <Activity className="h-5 w-5 animate-pulse" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <h1 className="font-bold text-sm tracking-wider text-foreground uppercase">
                Scraper Swarm
              </h1>
              <span className="text-[9px] px-1.5 py-0.2 rounded bg-primary/20 text-primary border border-primary/40 font-mono font-bold">
                PRO
              </span>
            </div>
            <div className="text-[10px] text-muted-foreground flex items-center gap-1.5 mt-0.5">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  activeCount > 0 ? "bg-status-ok animate-pulse" : "bg-status-warn"
                }`}
              />
              <span>{activeCount > 0 ? `${activeCount} Pods Active` : "Cluster Idle"}</span>
            </div>
          </div>
        </div>

        {/* 1Panel-Style Sidebar Navigation */}
        <nav className="space-y-1 text-xs">
          <div className="px-2 pb-1.5 text-[10px] uppercase font-bold tracking-wider text-muted-foreground/70">
            Control Center
          </div>

          <button
            onClick={() => setActiveTab("dashboard")}
            className={`w-full flex items-center justify-between px-3 py-2.5 rounded-lg transition-all ${
              activeTab === "dashboard"
                ? "bg-primary text-primary-foreground font-semibold shadow-md shadow-primary/20"
                : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
            }`}
          >
            <div className="flex items-center space-x-2.5">
              <LayoutDashboard className="h-4 w-4" />
              <span>Overview</span>
            </div>
            {activeCount > 0 && (
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded-full font-mono font-bold ${
                  activeTab === "dashboard"
                    ? "bg-black/30 text-white"
                    : "bg-status-ok/20 text-status-ok"
                }`}
              >
                {activeCount}
              </span>
            )}
          </button>

          <button
            onClick={() => setActiveTab("catalog")}
            className={`w-full flex items-center justify-between px-3 py-2.5 rounded-lg transition-all ${
              activeTab === "catalog"
                ? "bg-primary text-primary-foreground font-semibold shadow-md shadow-primary/20"
                : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
            }`}
          >
            <div className="flex items-center space-x-2.5">
              <Layers className="h-4 w-4" />
              <span>Engine App Store</span>
            </div>
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded-full font-mono ${
                activeTab === "catalog"
                  ? "bg-black/30 text-white"
                  : "bg-secondary text-muted-foreground"
              }`}
            >
              8+
            </span>
          </button>

          <button
            onClick={() => setActiveTab("containers")}
            className={`w-full flex items-center justify-between px-3 py-2.5 rounded-lg transition-all ${
              activeTab === "containers"
                ? "bg-primary text-primary-foreground font-semibold shadow-md shadow-primary/20"
                : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
            }`}
          >
            <div className="flex items-center space-x-2.5">
              <Boxes className="h-4 w-4" />
              <span>Containers & Stacks</span>
            </div>
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                activeTab === "containers"
                  ? "bg-black/30 text-white"
                  : "bg-secondary text-muted-foreground"
              }`}
            >
              Docker
            </span>
          </button>

          <div className="pt-4 px-2 pb-1.5 text-[10px] uppercase font-bold tracking-wider text-muted-foreground/70">
            Security & Integration
          </div>

          <button
            onClick={() => setActiveTab("agents")}
            className={`w-full flex items-center justify-between px-3 py-2.5 rounded-lg transition-all ${
              activeTab === "agents"
                ? "bg-primary text-primary-foreground font-semibold shadow-md shadow-primary/20"
                : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
            }`}
          >
            <div className="flex items-center space-x-2.5">
              <Cpu className="h-4 w-4" />
              <span>Agent Workbench</span>
            </div>
            {agentKeysCount > 0 && (
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded-full font-mono ${
                  activeTab === "agents"
                    ? "bg-black/30 text-white"
                    : "bg-secondary text-muted-foreground"
                }`}
              >
                {agentKeysCount}
              </span>
            )}
          </button>

          <button
            onClick={() => setActiveTab("security")}
            className={`w-full flex items-center justify-between px-3 py-2.5 rounded-lg transition-all ${
              activeTab === "security"
                ? "bg-primary text-primary-foreground font-semibold shadow-md shadow-primary/20"
                : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
            }`}
          >
            <div className="flex items-center space-x-2.5">
              <ShieldCheck className="h-4 w-4" />
              <span>Security Center</span>
            </div>
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded font-mono font-bold ${
                activeTab === "security"
                  ? "bg-black/30 text-white"
                  : "bg-status-ok/20 text-status-ok"
              }`}
            >
              {securityPosture ? `${securityPosture.score}%` : "100%"}
            </span>
          </button>

          <button
            onClick={() => setActiveTab("wizard")}
            className={`w-full flex items-center justify-between px-3 py-2.5 rounded-lg transition-all ${
              activeTab === "wizard"
                ? "bg-primary text-primary-foreground font-semibold shadow-md shadow-primary/20"
                : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
            }`}
          >
            <div className="flex items-center space-x-2.5">
              <Terminal className="h-4 w-4" />
              <span>Setup Wizard</span>
            </div>
            <span
              className={`text-[9px] px-1.5 py-0.5 rounded ${
                setupCompleted
                  ? "bg-status-ok/20 text-status-ok"
                  : "bg-status-warn/20 text-status-warn animate-pulse"
              }`}
            >
              {setupCompleted ? "Ready" : "Pending"}
            </span>
          </button>
        </nav>
      </div>

      {/* 1Panel-Style User Profile & System Status Footer */}
      <div className="space-y-3 pt-4 border-t border-border">
        {/* User Card */}
        <div className="p-2.5 rounded-lg bg-secondary/30 border border-border flex items-center justify-between">
          <div className="flex items-center space-x-2 min-w-0">
            <div className="h-7 w-7 rounded-full bg-primary/20 border border-primary/40 flex items-center justify-center text-primary font-bold text-xs">
              {authenticated ? username.charAt(0).toUpperCase() : "G"}
            </div>
            <div className="min-w-0">
              <div className="text-xs font-semibold text-foreground truncate">
                {authenticated ? username : "Guest User"}
              </div>
              <div className="text-[10px] text-muted-foreground truncate">
                {authenticated ? "Administrator" : "Read-Only Mode"}
              </div>
            </div>
          </div>

          {authenticated ? (
            <button
              onClick={onLogout}
              title="Sign Out"
              className="p-1.5 rounded text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
            >
              <LogOut className="h-4 w-4" />
            </button>
          ) : (
            <button
              onClick={onOpenLogin}
              title="Sign In with TOTP"
              className="p-1.5 rounded text-primary hover:bg-primary/20 transition-colors flex items-center gap-1 text-[11px] font-semibold"
            >
              <LogIn className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* Security Posture Mini Meter */}
        <div className="px-2.5 py-1.5 rounded bg-secondary/15 border border-border/60 text-[10px] flex items-center justify-between text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <Shield className="h-3 w-3 text-status-ok" />
            <span>SSRF Egress Guard</span>
          </span>
          <span className="text-status-ok font-mono font-semibold">Fail-Closed</span>
        </div>
      </div>
    </aside>
  );
};
