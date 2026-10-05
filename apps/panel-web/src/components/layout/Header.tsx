import React from "react";
import {
  FlaskConical,
  Play,
  Lock,
  LogIn,
  LogOut,
  Shield,
} from "lucide-react";
import { NavTab } from "./Sidebar";
import { ContainerStatus, SecurityPosture } from "../../types";

interface HeaderProps {
  activeTab: NavTab;
  authenticated: boolean;
  username: string;
  setupCompleted: boolean | null;
  liveContainers: ContainerStatus[];
  securityPosture: SecurityPosture | null;
  clusterTestRunning: boolean;
  deployingCore: boolean;
  onRunClusterTest: () => void;
  onDeployCore: () => void;
  onOpenLogin: () => void;
  onLogout: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  authenticated,
  username,
  setupCompleted,
  liveContainers,
  securityPosture,
  clusterTestRunning,
  deployingCore,
  onRunClusterTest,
  onDeployCore,
  onOpenLogin,
  onLogout,
}) => {
  const tabTitles: Record<NavTab, string> = {
    dashboard: "Overview & Telemetry",
    catalog: "Engine App Store",
    containers: "Containers & Stacks",
    agents: "Agent Workbench & MCP",
    security: "Security & Audit Center",
    wizard: "System Setup Wizard",
  };

  return (
    <header className="h-16 border-b border-border bg-card/70 flex items-center justify-between px-6 glass select-none">
      {/* Breadcrumb & Navigation Context */}
      <div className="flex items-center space-x-4">
        <div className="flex items-center space-x-2 text-xs">
          <span className="text-muted-foreground uppercase tracking-widest text-[11px]">System</span>
          <span className="text-muted-foreground">/</span>
          <strong className="text-foreground tracking-wide font-semibold text-xs uppercase">
            {tabTitles[activeTab]}
          </strong>
        </div>

        <span className="text-muted-foreground/40">|</span>

        {/* Live Telemetry Chips */}
        <div className="hidden lg:flex items-center space-x-2">
          <div className="px-2.5 py-1 rounded-full bg-secondary/40 border border-border text-[11px] flex items-center gap-1.5">
            <span className="text-muted-foreground">Stack:</span>
            <span className="text-status-ok font-semibold">
              {setupCompleted ? "Initialized" : "Pending"}
            </span>
          </div>

          <div className="px-2.5 py-1 rounded-full bg-secondary/40 border border-border text-[11px] flex items-center gap-1.5">
            <span className="text-muted-foreground">Containers:</span>
            <span className="text-foreground font-mono font-bold">
              {liveContainers.length} Active
            </span>
          </div>

          <div className="px-2.5 py-1 rounded-full bg-secondary/40 border border-border text-[11px] flex items-center gap-1.5">
            <Shield className="h-3 w-3 text-status-ok" />
            <span className="text-status-ok font-semibold">
              {securityPosture ? `${securityPosture.score}% Secure` : "Fail-Closed Egress"}
            </span>
          </div>
        </div>
      </div>

      {/* Header Actions & Auth Status */}
      <div className="flex items-center space-x-3">
        {/* Cluster Smoke Test CTA */}
        <button
          onClick={onRunClusterTest}
          disabled={clusterTestRunning || !authenticated}
          className="px-3 py-1.5 rounded-lg border border-border bg-secondary/50 text-foreground text-xs hover:bg-secondary flex items-center gap-1.5 transition-colors disabled:opacity-50"
          title={!authenticated ? "Sign in required" : "Run automated diagnostic smoke tests across all running engines"}
        >
          <FlaskConical className={`h-3.5 w-3.5 ${clusterTestRunning ? "animate-spin text-primary" : "text-primary"}`} />
          <span>{clusterTestRunning ? "Testing..." : "Cluster Smoke Test"}</span>
        </button>

        {/* Quick Deploy Core CTA */}
        <button
          onClick={onDeployCore}
          disabled={deployingCore || !authenticated}
          className="px-3.5 py-1.5 rounded-lg bg-primary text-primary-foreground font-semibold text-xs flex items-center gap-1.5 hover:bg-primary/90 transition-all shadow-md shadow-primary/20 disabled:opacity-50"
          title={!authenticated ? "Sign in required" : "Deploy SearXNG, Crawl4AI, Valkey and Egress Web stack"}
        >
          <Play className={`h-3.5 w-3.5 ${deployingCore ? "animate-pulse" : ""}`} />
          <span>{deployingCore ? "Deploying..." : "Quick Deploy Core"}</span>
        </button>

        {/* Auth / Profile Pill */}
        <div className="border-l border-border pl-3 flex items-center space-x-2">
          {authenticated ? (
            <div className="flex items-center space-x-2">
              <div className="h-7 w-7 rounded-full bg-primary/20 border border-primary/40 flex items-center justify-center text-primary font-bold text-xs">
                {username.charAt(0).toUpperCase()}
              </div>
              <button
                onClick={onLogout}
                className="text-muted-foreground hover:text-foreground text-xs flex items-center gap-1"
                title="Sign Out"
              >
                <LogOut className="h-3.5 w-3.5" />
              </button>
            </div>
          ) : (
            <button
              onClick={onOpenLogin}
              className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground font-semibold text-xs flex items-center gap-1.5 hover:bg-primary/90 transition-colors"
            >
              <LogIn className="h-3.5 w-3.5" />
              <span>Sign In</span>
            </button>
          )}

          <div className="hidden sm:flex items-center space-x-1.5 text-[11px] text-muted-foreground border-l border-border pl-3">
            <Lock className="h-3.5 w-3.5 text-primary" />
            <span>HTTPS Isolated</span>
          </div>
        </div>
      </div>
    </header>
  );
};
