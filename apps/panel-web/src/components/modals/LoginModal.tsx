import React, { useState } from "react";
import { Lock, X, LogIn } from "lucide-react";

interface LoginModalProps {
  onClose: () => void;
  onLogin: (username: string, code: string) => Promise<boolean>;
}

export const LoginModal: React.FC<LoginModalProps> = ({ onClose, onLogin }) => {
  const [username, setUsername] = useState("admin");
  const [totpCode, setTotpCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!username || totpCode.length !== 6) return;

    setLoading(true);
    setError(null);
    try {
      const ok = await onLogin(username, totpCode);
      if (ok) {
        onClose();
      }
    } catch (err: any) {
      setError(err.message || "Authentication failed. Check TOTP code.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4 backdrop-blur-sm animate-in fade-in">
      <div className="w-full max-w-md bg-card border border-primary/40 rounded-xl p-6 space-y-5 shadow-2xl glass">
        <div className="flex items-center justify-between border-b border-border/50 pb-3">
          <div className="flex items-center space-x-2.5">
            <div className="h-8 w-8 rounded-lg bg-primary/10 border border-primary/40 flex items-center justify-center text-primary">
              <Lock className="h-4 w-4" />
            </div>
            <h3 className="text-sm font-bold uppercase tracking-wider text-foreground">
              Administrator Authentication
            </h3>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {error && (
          <div className="p-3 rounded-lg bg-status-critical/10 border border-status-critical/40 text-status-critical text-xs leading-relaxed">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4 text-xs">
          <div>
            <label className="text-muted-foreground block uppercase text-[10px] font-bold tracking-wider mb-1">
              Admin Username
            </label>
            <input
              type="text"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full bg-input border border-border rounded-lg px-3 py-2 text-xs font-mono"
            />
          </div>

          <div>
            <label className="text-muted-foreground block uppercase text-[10px] font-bold tracking-wider mb-1">
              6-Digit Authenticator Code (TOTP)
            </label>
            <input
              type="text"
              required
              maxLength={6}
              placeholder="123456"
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, ""))}
              className="w-full bg-input border border-border rounded-lg px-3 py-2 text-center text-base font-mono tracking-widest text-primary focus:border-primary"
            />
          </div>

          <div className="pt-2 flex items-center justify-end space-x-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 bg-secondary text-foreground text-xs rounded-lg hover:bg-secondary/80 border border-border transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading || totpCode.length !== 6}
              className="px-5 py-2 bg-primary text-primary-foreground font-semibold text-xs rounded-lg hover:bg-primary/90 transition-all flex items-center gap-1.5 shadow-md shadow-primary/20 disabled:opacity-50"
            >
              <LogIn className="h-3.5 w-3.5" />
              <span>{loading ? "Authenticating..." : "Verify & Sign In"}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
