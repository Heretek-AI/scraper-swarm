import { useState } from "react";
import { Terminal, CheckCircle2 } from "lucide-react";

interface WizardViewProps {
  setupCompleted: boolean | null;
  onBootstrapInit: (token: string, username: string) => Promise<{ totpSecret: string; totpUri: string }>;
  onVerifyTotp: (username: string, code: string) => Promise<boolean>;
}

export const WizardView: React.FC<WizardViewProps> = ({
  setupCompleted,
  onBootstrapInit,
  onVerifyTotp,
}) => {
  const [bootstrapToken, setBootstrapToken] = useState("");
  const [adminUsername, setAdminUsername] = useState("admin");
  const [totpSecret, setTotpSecret] = useState<string | null>(null);
  const [totpUri, setTotpUri] = useState<string | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  async function handleInitSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!bootstrapToken) return;
    setLoading(true);
    setMessage("");
    try {
      const data = await onBootstrapInit(bootstrapToken, adminUsername);
      setTotpSecret(data.totpSecret);
      setTotpUri(data.totpUri);
      setMessage("Bootstrap token verified. Enter TOTP code to establish identity.");
    } catch (err: any) {
      setMessage(`Initialization error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleVerifySubmit(e: React.FormEvent) {
    e.preventDefault();
    if (totpCode.length !== 6) return;
    setLoading(true);
    try {
      const ok = await onVerifyTotp(adminUsername, totpCode);
      if (ok) {
        setMessage("Administrator setup completed successfully.");
      }
    } catch (err: any) {
      setMessage(`Verification error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="max-w-2xl mx-auto py-8 space-y-6">
      <div className="text-center space-y-2">
        <div className="h-12 w-12 rounded-xl border border-primary/50 bg-primary/10 flex items-center justify-center text-primary mx-auto mb-2 corruption-glow">
          <Terminal className="h-6 w-6" />
        </div>
        <h2 className="text-lg font-bold uppercase tracking-wider text-foreground">
          Control Plane Initialization Wizard
        </h2>
        <p className="text-xs text-muted-foreground">
          Establish root cryptographic identity and activate mandatory TOTP 2FA.
        </p>
      </div>

      {setupCompleted ? (
        <div className="p-8 rounded-xl border border-status-ok/40 bg-card/60 glass text-center space-y-4">
          <div className="h-12 w-12 rounded-full bg-status-ok/10 border border-status-ok/40 flex items-center justify-center text-status-ok mx-auto">
            <CheckCircle2 className="h-6 w-6" />
          </div>
          <h3 className="text-sm font-bold uppercase text-foreground">
            Control Plane Fully Initialized
          </h3>
          <p className="text-xs text-muted-foreground max-w-md mx-auto leading-relaxed">
            The root administrator identity has been securely provisioned with envelope encryption. All subsequent administrative interactions require valid TOTP 2FA session verification.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {message && (
            <div className="p-3.5 rounded-lg bg-primary/10 border border-primary/40 text-xs text-primary leading-relaxed">
              {message}
            </div>
          )}

          {!totpSecret ? (
            <form onSubmit={handleInitSubmit} className="p-6 rounded-xl border border-border bg-card/60 glass space-y-4">
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground uppercase font-semibold">
                  Bootstrap Token
                </label>
                <input
                  type="password"
                  required
                  placeholder="Paste terminal bootstrap token..."
                  value={bootstrapToken}
                  onChange={(e) => setBootstrapToken(e.target.value)}
                  className="w-full bg-input border border-border rounded-lg px-3 py-2 text-xs font-mono"
                />
              </div>

              <div className="space-y-1">
                <label className="text-xs text-muted-foreground uppercase font-semibold">
                  Admin Username
                </label>
                <input
                  type="text"
                  required
                  value={adminUsername}
                  onChange={(e) => setAdminUsername(e.target.value)}
                  className="w-full bg-input border border-border rounded-lg px-3 py-2 text-xs font-mono"
                />
              </div>

              <button
                type="submit"
                disabled={loading || !bootstrapToken}
                className="w-full py-2.5 bg-primary text-primary-foreground font-semibold text-xs rounded-lg hover:bg-primary/90 transition-all disabled:opacity-50 shadow-md shadow-primary/20"
              >
                {loading ? "Verifying Bootstrap Token..." : "Provision Admin Identity"}
              </button>
            </form>
          ) : (
            <form onSubmit={handleVerifySubmit} className="p-6 rounded-xl border border-border bg-card/60 glass space-y-4">
              <div className="text-center space-y-2">
                <div className="text-xs font-bold text-foreground">Scan Authenticator Key</div>
                <div className="p-3 bg-secondary/40 rounded-lg border border-border font-mono text-xs text-primary break-all select-all">
                  {totpSecret}
                </div>
                {totpUri && (
                  <div className="text-[10px] text-muted-foreground break-all select-all font-mono">
                    URI: {totpUri}
                  </div>
                )}
              </div>

              <div className="space-y-1">
                <label className="text-xs text-muted-foreground uppercase font-semibold text-center block">
                  Enter 6-Digit TOTP Code
                </label>
                <input
                  type="text"
                  maxLength={6}
                  required
                  placeholder="123456"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, ""))}
                  className="w-full bg-input border border-border rounded-lg px-3 py-2 text-center text-sm font-mono tracking-widest text-primary focus:border-primary"
                />
              </div>

              <button
                type="submit"
                disabled={loading || totpCode.length !== 6}
                className="w-full py-2.5 bg-primary text-primary-foreground font-semibold text-xs rounded-lg hover:bg-primary/90 transition-all disabled:opacity-50 shadow-md shadow-primary/20"
              >
                {loading ? "Verifying..." : "Complete Setup & Unlock Control Deck"}
              </button>
            </form>
          )}
        </div>
      )}
    </div>
  );
};
