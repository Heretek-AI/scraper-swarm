import React, { createContext, useContext, useState, useCallback } from "react";
import { CheckCircle2, AlertTriangle, AlertCircle, Info, X } from "lucide-react";

type ToastType = "success" | "error" | "warning" | "info";

interface ToastItem {
  id: string;
  type: ToastType;
  message: string;
}

interface ToastContextValue {
  toast: {
    success: (msg: string) => void;
    error: (msg: string) => void;
    warning: (msg: string) => void;
    info: (msg: string) => void;
  };
}

const ToastContext = createContext<ToastContextValue | null>(null);

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const addToast = useCallback((type: ToastType, message: string) => {
    const id = Math.random().toString(36).substring(2, 9);
    setToasts((prev) => [...prev, { id, type, message }]);

    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4500);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = {
    success: (msg: string) => addToast("success", msg),
    error: (msg: string) => addToast("error", msg),
    warning: (msg: string) => addToast("warning", msg),
    info: (msg: string) => addToast("info", msg),
  };

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="fixed bottom-5 right-5 z-50 flex flex-col space-y-2 pointer-events-none max-w-sm w-full">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`pointer-events-auto flex items-start justify-between p-3.5 rounded-lg shadow-xl border backdrop-blur-md transition-all animate-in slide-in-from-bottom-2 ${
              t.type === "success"
                ? "bg-status-ok/15 border-status-ok/40 text-foreground"
                : t.type === "error"
                ? "bg-status-critical/15 border-status-critical/40 text-foreground"
                : t.type === "warning"
                ? "bg-status-warn/15 border-status-warn/40 text-foreground"
                : "bg-card/90 border-border text-foreground"
            }`}
          >
            <div className="flex items-start space-x-2.5">
              <span className="mt-0.5">
                {t.type === "success" && <CheckCircle2 className="h-4 w-4 text-status-ok" />}
                {t.type === "error" && <AlertCircle className="h-4 w-4 text-status-critical" />}
                {t.type === "warning" && <AlertTriangle className="h-4 w-4 text-status-warn" />}
                {t.type === "info" && <Info className="h-4 w-4 text-primary" />}
              </span>
              <p className="text-xs leading-relaxed font-sans">{t.message}</p>
            </div>
            <button
              onClick={() => removeToast(t.id)}
              className="text-muted-foreground hover:text-foreground p-0.5 ml-2"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
};

export const useToast = () => {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return context.toast;
};
