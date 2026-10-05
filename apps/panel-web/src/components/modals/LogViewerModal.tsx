import React, { useState, useEffect } from "react";
import { Terminal, X, RefreshCw, Copy, Check } from "lucide-react";

interface LogViewerModalProps {
  serviceId: string;
  onClose: () => void;
}

export const LogViewerModal: React.FC<LogViewerModalProps> = ({ serviceId, onClose }) => {
  const [logs, setLogs] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [lines, setLines] = useState<number>(100);
  const [copied, setCopied] = useState<boolean>(false);

  useEffect(() => {
    fetchLogs();
  }, [serviceId, lines]);

  async function fetchLogs() {
    setLoading(true);
    try {
      const res = await fetch(`/services/${serviceId}/logs?lines=${lines}`);
      if (res.ok) {
        const data = await res.json();
        setLogs(data.logs || "No log output recorded.");
      } else {
        setLogs("Failed to fetch logs. Container may not be running.");
      }
    } catch (e: any) {
      setLogs(`Error connecting to swarmd socket: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  function handleCopy() {
    navigator.clipboard.writeText(logs);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4 backdrop-blur-sm animate-in fade-in">
      <div className="w-full max-w-4xl bg-card border border-primary/40 rounded-xl shadow-2xl flex flex-col h-[75vh] glass overflow-hidden">
        {/* Header */}
        <div className="p-4 border-b border-border flex items-center justify-between bg-secondary/30">
          <div className="flex items-center space-x-2.5">
            <div className="h-7 w-7 rounded bg-primary/10 border border-primary/40 flex items-center justify-center text-primary">
              <Terminal className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-foreground">
                Container Console Logs: {serviceId}
              </h3>
              <div className="text-[10px] text-muted-foreground font-mono">
                docker compose logs --tail={lines} {serviceId}
              </div>
            </div>
          </div>

          <div className="flex items-center space-x-2">
            <select
              value={lines}
              onChange={(e) => setLines(Number(e.target.value))}
              className="bg-input border border-border rounded px-2 py-1 text-xs font-mono"
            >
              <option value={50}>50 lines</option>
              <option value={100}>100 lines</option>
              <option value={300}>300 lines</option>
              <option value={1000}>1000 lines</option>
            </select>

            <button
              onClick={handleCopy}
              className="p-1.5 rounded bg-secondary text-foreground hover:bg-secondary/80 border border-border transition-colors text-xs flex items-center gap-1"
              title="Copy to clipboard"
            >
              {copied ? <Check className="h-3.5 w-3.5 text-status-ok" /> : <Copy className="h-3.5 w-3.5" />}
            </button>

            <button
              onClick={fetchLogs}
              disabled={loading}
              className="p-1.5 rounded bg-secondary text-foreground hover:bg-secondary/80 border border-border transition-colors text-xs flex items-center gap-1"
              title="Refresh logs"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin text-primary" : ""}`} />
            </button>

            <button
              onClick={onClose}
              className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Log Viewer Body */}
        <div className="flex-1 p-4 bg-background/95 overflow-y-auto font-mono text-[11px] text-foreground leading-relaxed whitespace-pre-wrap select-all">
          {loading ? (
            <span className="text-muted-foreground">Streaming logs from Docker daemon via swarmd...</span>
          ) : (
            logs
          )}
        </div>

        {/* Footer */}
        <div className="p-3 border-t border-border bg-secondary/15 flex items-center justify-between text-[11px] text-muted-foreground">
          <span>Target Service: <code>{serviceId}</code></span>
          <span>Mediated through privileged <code>swarmd.sock</code></span>
        </div>
      </div>
    </div>
  );
};
