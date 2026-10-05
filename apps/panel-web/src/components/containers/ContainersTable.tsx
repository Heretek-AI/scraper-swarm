import React from "react";
import { FlaskConical, Terminal, RotateCw } from "lucide-react";
import { ContainerStatus } from "../../types";

interface ContainersTableProps {
  containers: ContainerStatus[];
  testingServiceId: string | null;
  authenticated: boolean;
  showContainerId?: boolean;
  onRunSmokeTest: (serviceId: string) => void;
  onOpenLogs: (serviceId: string) => void;
  onRestartService: (serviceId: string) => void;
  onOpenLogin: () => void;
  emptyMessage?: string;
}

interface ContainerActionsProps {
  serviceId: string;
  isTesting: boolean;
  authenticated: boolean;
  onRunSmokeTest: (serviceId: string) => void;
  onOpenLogs: (serviceId: string) => void;
  onRestartService: (serviceId: string) => void;
  onOpenLogin: () => void;
}

const ContainerActions: React.FC<ContainerActionsProps> = ({
  serviceId,
  isTesting,
  authenticated,
  onRunSmokeTest,
  onOpenLogs,
  onRestartService,
  onOpenLogin,
}) => (
  <div className="flex items-center justify-end space-x-2">
    <button
      onClick={() => (authenticated ? onRunSmokeTest(serviceId) : onOpenLogin())}
      disabled={isTesting}
      className="px-2.5 py-1 bg-secondary text-primary border border-primary/30 rounded text-[11px] hover:bg-primary/10 transition-colors flex items-center gap-1 disabled:opacity-50"
      title={!authenticated ? "Sign in to run test" : "Execute engine smoke test"}
    >
      <FlaskConical className={`h-3 w-3 ${isTesting ? "animate-spin" : ""}`} />
      <span>{isTesting ? "Testing..." : "Smoke Test"}</span>
    </button>

    <button
      onClick={() => (authenticated ? onOpenLogs(serviceId) : onOpenLogin())}
      className="px-2.5 py-1 bg-secondary text-foreground border border-border rounded text-[11px] hover:bg-secondary/80 transition-colors flex items-center gap-1"
      title={!authenticated ? "Sign in to view logs" : "View container logs"}
    >
      <Terminal className="h-3 w-3" />
      <span>Logs</span>
    </button>

    <button
      onClick={() => (authenticated ? onRestartService(serviceId) : onOpenLogin())}
      className="px-2.5 py-1 bg-secondary text-foreground border border-border rounded text-[11px] hover:bg-secondary/80 transition-colors flex items-center gap-1"
      title={!authenticated ? "Sign in to restart" : "Restart container"}
    >
      <RotateCw className="h-3 w-3" />
      <span>Restart</span>
    </button>
  </div>
);

interface ContainerRowProps {
  container: ContainerStatus;
  index: number;
  testingServiceId: string | null;
  authenticated: boolean;
  showContainerId?: boolean;
  onRunSmokeTest: (serviceId: string) => void;
  onOpenLogs: (serviceId: string) => void;
  onRestartService: (serviceId: string) => void;
  onOpenLogin: () => void;
}

const ContainerRow: React.FC<ContainerRowProps> = ({
  container,
  index,
  testingServiceId,
  authenticated,
  showContainerId,
  onRunSmokeTest,
  onOpenLogs,
  onRestartService,
  onOpenLogin,
}) => {
  const sId = container.Service || container.Name?.replace("scraper-swarm-", "") || `unknown-${index}`;
  const isTesting = testingServiceId === sId;

  return (
    <tr className="hover:bg-secondary/20 transition-colors">
      <td className="py-3.5 pr-4 font-sans">
        <div className="flex items-center space-x-2.5">
          <span className="h-2 w-2 rounded-full bg-status-ok animate-pulse" />
          <span className="font-bold text-foreground uppercase tracking-wide font-mono">
            {sId}
          </span>
        </div>
      </td>
      {showContainerId && (
        <td className="py-3.5 pr-4 text-muted-foreground">
          {container.ID ? container.ID.substring(0, 12) : "n/a"}
        </td>
      )}
      <td className="py-3.5 pr-4 text-muted-foreground truncate max-w-xs font-mono text-[11px]">
        {container.Image}
      </td>
      <td className="py-3.5 pr-4 font-sans">
        <span className="px-2 py-0.5 rounded text-[10px] uppercase font-semibold bg-status-ok/10 text-status-ok border border-status-ok/30">
          {container.State || "running"}
        </span>
      </td>
      <td className="py-3.5 pr-4 text-muted-foreground font-mono text-[11px]">
        {container.Ports || "Mesh Internal"}
      </td>
      <td className="py-3.5 text-right font-sans">
        <ContainerActions
          serviceId={sId}
          isTesting={isTesting}
          authenticated={authenticated}
          onRunSmokeTest={onRunSmokeTest}
          onOpenLogs={onOpenLogs}
          onRestartService={onRestartService}
          onOpenLogin={onOpenLogin}
        />
      </td>
    </tr>
  );
};

export const ContainersTable: React.FC<ContainersTableProps> = ({
  containers,
  testingServiceId,
  authenticated,
  showContainerId = false,
  onRunSmokeTest,
  onOpenLogs,
  onRestartService,
  onOpenLogin,
  emptyMessage = "No engine containers running currently.",
}) => {
  if (containers.length === 0) {
    return (
      <div className="text-center py-10 text-muted-foreground text-xs border border-dashed border-border rounded-xl">
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-border text-muted-foreground uppercase text-[10px] tracking-wider">
            <th className="pb-3 font-semibold">Container / Engine</th>
            {showContainerId && <th className="pb-3 font-semibold">Container ID</th>}
            <th className="pb-3 font-semibold">Image Tag</th>
            <th className="pb-3 font-semibold">Status</th>
            <th className="pb-3 font-semibold">Internal Bindings</th>
            <th className="pb-3 font-semibold text-right">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40 font-mono text-[11px]">
          {containers.map((c, idx) => (
            <ContainerRow
              key={c.ID || idx}
              container={c}
              index={idx}
              testingServiceId={testingServiceId}
              authenticated={authenticated}
              showContainerId={showContainerId}
              onRunSmokeTest={onRunSmokeTest}
              onOpenLogs={onOpenLogs}
              onRestartService={onRestartService}
              onOpenLogin={onOpenLogin}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
};
