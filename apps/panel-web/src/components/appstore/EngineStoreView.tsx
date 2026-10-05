import { useState, useMemo } from "react";
import {
  Search,
  Settings,
  Eye,
  Terminal,
  Layers,
  Lock,
} from "lucide-react";
import { ServiceItem, ContainerStatus, InstalledServiceInfo } from "../../types";

interface EngineStoreViewProps {
  catalog: ServiceItem[];
  liveContainers: ContainerStatus[];
  installedServices: Record<string, InstalledServiceInfo>;
  authenticated: boolean;
  onOpenConfig: (service: ServiceItem) => void;
  onOpenLogs: (serviceId: string) => void;
  onOpenLogin: () => void;
}

export const EngineStoreView: React.FC<EngineStoreViewProps> = ({
  catalog,
  liveContainers,
  installedServices,
  authenticated,
  onOpenConfig,
  onOpenLogs,
  onOpenLogin,
}) => {
  const [activeCategory, setActiveCategory] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");

  const categories = [
    { id: "all", label: "All Engines" },
    { id: "core", label: "Core Search & Extraction" },
    { id: "stealth", label: "Stealth & Antidetect" },
    { id: "agents", label: "Autonomous AI Research" },
    { id: "support", label: "Support & Infrastructure" },
  ];

  const filteredCatalog = useMemo(() => {
    return catalog.filter((item) => {
      // Category filter
      if (activeCategory === "core" && item.tier !== "core") return false;
      if (activeCategory === "stealth" && !["scrapling", "cloakbrowser"].includes(item.id)) return false;
      if (activeCategory === "agents" && !["gpt-researcher", "cyberscraper"].includes(item.id)) return false;
      if (activeCategory === "support" && item.tier !== "support") return false;

      // Text query
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        return (
          item.name.toLowerCase().includes(q) ||
          item.id.toLowerCase().includes(q) ||
          item.license.spdx.toLowerCase().includes(q) ||
          item.tier.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [catalog, activeCategory, searchQuery]);

  return (
    <div className="space-y-6">
      {/* 1Panel-Style App Store Header & Search */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center space-x-2">
            <Layers className="h-5 w-5 text-primary" />
            <h2 className="text-base font-bold uppercase tracking-wider text-foreground">
              Search & Scraping Engine Store
            </h2>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            1Panel-style multi-profile management, seccomp sandboxing, and fail-closed egress.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div className="relative">
            <Search className="h-4 w-4 absolute left-3 top-2.5 text-muted-foreground" />
            <input
              type="text"
              placeholder="Search engines, licenses..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9 pr-3 py-1.5 bg-input border border-border rounded-lg text-xs w-56 focus:w-72 transition-all font-mono"
            />
          </div>
        </div>
      </div>

      {/* Guest Mode Notification Bar if unauthenticated */}
      {!authenticated && (
        <div className="p-3.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-xs text-amber-400 flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Lock className="h-4 w-4" />
            You are browsing the engine store in <strong>Read-Only Guest Mode</strong>. Sign in to customize parameters and deploy engines.
          </span>
          <button
            onClick={onOpenLogin}
            className="px-3 py-1 bg-amber-500 text-black font-semibold text-[11px] rounded-lg hover:bg-amber-400 transition-colors"
          >
            Sign In with TOTP
          </button>
        </div>
      )}

      {/* Category Filter Pills */}
      <div className="flex items-center space-x-2 border-b border-border pb-3 overflow-x-auto text-xs">
        {categories.map((c) => (
          <button
            key={c.id}
            onClick={() => setActiveCategory(c.id)}
            className={`px-3.5 py-1.5 rounded-lg whitespace-nowrap transition-all font-semibold ${
              activeCategory === c.id
                ? "bg-primary text-primary-foreground shadow-sm shadow-primary/20"
                : "bg-secondary/40 text-muted-foreground hover:bg-secondary hover:text-foreground"
            }`}
          >
            {c.label}
          </button>
        ))}
      </div>

      {/* 1Panel-Style Engine Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {filteredCatalog.map((item) => {
          const isRunning = liveContainers.some(
            (c) => c.Service === item.id || (c.Name && c.Name.includes(item.id))
          );
          const isInstalled = Boolean(installedServices[item.id]);
          const activeProfile = installedServices[item.id]?.profile || "standard";
          const standardRes = item.resources?.standard;

          return (
            <div
              key={item.id}
              className="p-5 rounded-xl border border-border bg-card/60 flex flex-col justify-between space-y-4 hover:border-primary/40 transition-all glass shadow-sm"
            >
              <div className="space-y-3.5">
                {/* Header: Avatar, Name, Status Pill */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2.5">
                    <div className="h-8 w-8 rounded-lg bg-primary/10 border border-primary/30 flex items-center justify-center font-bold text-xs uppercase text-primary font-mono">
                      {item.id.substring(0, 3)}
                    </div>
                    <div>
                      <h3 className="font-bold text-sm text-foreground uppercase tracking-wide">
                        {item.name}
                      </h3>
                      <div className="text-[10px] text-muted-foreground font-mono">
                        {item.id}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5">
                    <span className="px-2 py-0.5 rounded text-[10px] uppercase font-semibold bg-secondary text-muted-foreground border border-border">
                      {item.tier}
                    </span>
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] uppercase font-semibold flex items-center gap-1 ${
                        isRunning
                          ? "bg-status-ok/10 text-status-ok border border-status-ok/30"
                          : isInstalled
                          ? "bg-status-warn/10 text-status-warn border border-status-warn/30"
                          : "bg-secondary text-muted-foreground"
                      }`}
                    >
                      <span className={`h-1.5 w-1.5 rounded-full ${isRunning ? "bg-status-ok animate-pulse" : isInstalled ? "bg-status-warn" : "bg-muted-foreground/40"}`} />
                      {isRunning ? "Running" : isInstalled ? "Stopped" : "Available"}
                    </span>
                  </div>
                </div>

                {/* Specs & Hardware Footprint */}
                <div className="space-y-1.5 text-xs pt-1 border-t border-border/40">
                  <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                    <span>License:</span>
                    <span className="text-foreground font-mono font-semibold">{item.license.spdx}</span>
                  </div>
                  {standardRes && (
                    <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                      <span>Standard Footprint:</span>
                      <span className="text-foreground font-mono">
                        {standardRes.mem} RAM / {standardRes.cpus} CPU
                      </span>
                    </div>
                  )}
                  {isInstalled && (
                    <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                      <span>Active Profile:</span>
                      <span className="text-primary font-mono uppercase font-bold">{activeProfile}</span>
                    </div>
                  )}
                  {item.requires && item.requires.length > 0 && (
                    <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                      <span>Dependencies:</span>
                      <span className="text-status-warn font-mono">{item.requires.join(", ")}</span>
                    </div>
                  )}
                </div>
              </div>

              {/* Action Buttons */}
              <div className="pt-2 flex items-center space-x-2">
                <button
                  onClick={() => onOpenConfig(item)}
                  className={`flex-1 py-2 text-xs font-semibold rounded-lg border flex items-center justify-center space-x-1.5 transition-colors ${
                    authenticated
                      ? "bg-secondary/80 hover:bg-secondary text-foreground border-border hover:border-primary/50"
                      : "bg-secondary/40 text-muted-foreground border-border/60 hover:text-foreground hover:border-primary/40"
                  }`}
                >
                  {authenticated ? (
                    <>
                      <Settings className="h-3.5 w-3.5 text-primary" />
                      <span>Configure & Deploy</span>
                    </>
                  ) : (
                    <>
                      <Eye className="h-3.5 w-3.5 text-amber-400" />
                      <span>View Specifications</span>
                    </>
                  )}
                </button>

                {isRunning && (
                  <button
                    onClick={() => (authenticated ? onOpenLogs(item.id) : onOpenLogin())}
                    className="p-2 bg-secondary text-muted-foreground hover:text-foreground rounded-lg border border-border transition-colors"
                    title={!authenticated ? "Sign in to view logs" : "View container logs"}
                  >
                    <Terminal className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
