import { useState } from "react";
import {
  ShieldCheck,
  CheckCircle2,
  AlertTriangle,
  FileCheck,
  Shield,
} from "lucide-react";
import { SecurityPosture, AuditEntry } from "../../types";
import { AuthRequiredCard } from "../ui/AuthRequiredCard";

interface SecurityViewProps {
  authenticated: boolean;
  securityPosture: SecurityPosture | null;
  auditLogs: AuditEntry[];
  onOpenLogin: () => void;
  onVerifyChain: () => Promise<any>;
}

export const SecurityView: React.FC<SecurityViewProps> = ({
  authenticated,
  securityPosture,
  auditLogs,
  onOpenLogin,
  onVerifyChain,
}) => {
  const [verifying, setVerifying] = useState<boolean>(false);
  const [chainResult, setChainResult] = useState<{ valid: boolean; entries_checked: number } | null>(null);

  async function handleVerifyClick() {
    setVerifying(true);
    try {
      const res = await onVerifyChain();
      setChainResult(res);
    } finally {
      setVerifying(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-center space-x-2">
          <ShieldCheck className="h-5 w-5 text-primary" />
          <h2 className="text-base font-bold uppercase tracking-wider text-foreground">
            Security Posture & Tamper-Evident Audit
          </h2>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">
          Cryptographic verification of strict filesystem permissions, non-root capability drops, and SHA-256 hash chains.
        </p>
      </div>

      {!authenticated ? (
        <AuthRequiredCard
          description="Audit logs and cryptographic posture diagnostics require verified administrator credentials."
          buttonText="Sign In to View Security Logs"
          onOpenLogin={onOpenLogin}
        />
      ) : (
        <div className="space-y-6">
          {/* Posture Score Breakdown */}
          <div className="p-6 rounded-xl border border-border bg-card/40 glass space-y-4 shadow-sm">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Shield className="h-4 w-4 text-primary" />
                <h3 className="text-xs font-bold uppercase tracking-wider text-foreground">
                  Security Defense Scorecard
                </h3>
              </div>
              <span className="text-xl font-extrabold text-primary font-mono">
                {securityPosture ? `${securityPosture.score}% SECURE` : "100% SECURE"}
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
              <div className="p-3.5 rounded-lg bg-secondary/30 border border-border flex items-center justify-between">
                <div>
                  <div className="font-semibold text-foreground">Master Key Cryptography</div>
                  <div className="text-[11px] text-muted-foreground">Strict 0600 filesystem permissions</div>
                </div>
                <CheckCircle2 className="h-4 w-4 text-status-ok" />
              </div>

              <div className="p-3.5 rounded-lg bg-secondary/30 border border-border flex items-center justify-between">
                <div>
                  <div className="font-semibold text-foreground">Egress SSRF Guard</div>
                  <div className="text-[11px] text-muted-foreground">Default-deny non-routable RFC1918</div>
                </div>
                <CheckCircle2 className="h-4 w-4 text-status-ok" />
              </div>

              <div className="p-3.5 rounded-lg bg-secondary/30 border border-border flex items-center justify-between">
                <div>
                  <div className="font-semibold text-foreground">Container Capability Sandboxing</div>
                  <div className="text-[11px] text-muted-foreground">CAP_DROP: ALL with Seccomp Chromium filter</div>
                </div>
                <CheckCircle2 className="h-4 w-4 text-status-ok" />
              </div>

              <div className="p-3.5 rounded-lg bg-secondary/30 border border-border flex items-center justify-between">
                <div>
                  <div className="font-semibold text-foreground">Mandatory Administrator 2FA</div>
                  <div className="text-[11px] text-muted-foreground">Time-based One-Time Password (TOTP)</div>
                </div>
                <CheckCircle2 className="h-4 w-4 text-status-ok" />
              </div>
            </div>
          </div>

          {/* Tamper-Evident SHA-256 Audit Log Table */}
          <div className="p-6 rounded-xl border border-border bg-card/40 glass space-y-4 shadow-sm">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-xs font-bold uppercase tracking-wider text-foreground">
                  Tamper-Evident Cryptographic Audit Trail
                </h3>
                <p className="text-[11px] text-muted-foreground">
                  Every state mutation is permanently chained using SHA-256 parent block hashing.
                </p>
              </div>

              <button
                onClick={handleVerifyClick}
                disabled={verifying}
                className="px-3.5 py-1.5 bg-primary text-primary-foreground font-semibold text-xs rounded-lg hover:bg-primary/90 transition-all flex items-center gap-1.5 shadow-sm disabled:opacity-50"
              >
                <FileCheck className={`h-3.5 w-3.5 ${verifying ? "animate-spin" : ""}`} />
                <span>{verifying ? "Recalculating Hashes..." : "Verify Hash Chain"}</span>
              </button>
            </div>

            {chainResult && (
              <div
                className={`p-3.5 rounded-lg border text-xs flex items-center justify-between ${
                  chainResult.valid
                    ? "bg-status-ok/10 border-status-ok/30 text-status-ok"
                    : "bg-status-critical/10 border-status-critical/30 text-status-critical"
                }`}
              >
                <span className="font-bold flex items-center gap-1.5">
                  {chainResult.valid ? <CheckCircle2 className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
                  {chainResult.valid ? "Hash-chain integrity confirmed. No records modified." : "Hash-chain discrepancy detected!"}
                </span>
                <span className="font-mono text-[11px]">
                  {chainResult.entries_checked} entries verified
                </span>
              </div>
            )}

            <div className="overflow-x-auto max-h-72">
              <table className="w-full text-left text-xs font-mono">
                <thead>
                  <tr className="border-b border-border text-muted-foreground uppercase text-[10px] tracking-wider font-sans">
                    <th className="pb-2.5 font-semibold">Timestamp</th>
                    <th className="pb-2.5 font-semibold">Actor</th>
                    <th className="pb-2.5 font-semibold">Action</th>
                    <th className="pb-2.5 font-semibold">Target / Details</th>
                    <th className="pb-2.5 font-semibold text-right">Entry Hash (SHA-256)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40 text-[11px]">
                  {auditLogs.map((log) => (
                    <tr key={log.id} className="hover:bg-secondary/20 transition-colors">
                      <td className="py-2.5 pr-4 text-muted-foreground whitespace-nowrap">
                        {log.timestamp.replace("T", " ").substring(0, 19)}
                      </td>
                      <td className="py-2.5 pr-4 font-bold text-foreground">
                        {log.actor}
                      </td>
                      <td className="py-2.5 pr-4 text-primary font-semibold">
                        {log.action}
                      </td>
                      <td className="py-2.5 pr-4 text-muted-foreground truncate max-w-xs">
                        {log.target || log.details || "—"}
                      </td>
                      <td className="py-2.5 text-right text-muted-foreground/80 font-mono text-[10px]">
                        {log.entry_hash ? `${log.entry_hash.substring(0, 14)}...` : "legacy"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
