import React from "react";
import { Lock } from "lucide-react";

interface AuthRequiredCardProps {
  title?: string;
  description: string;
  buttonText: string;
  onOpenLogin: () => void;
}

export const AuthRequiredCard: React.FC<AuthRequiredCardProps> = ({
  title = "Authentication Required",
  description,
  buttonText,
  onOpenLogin,
}) => {
  return (
    <div className="p-6 rounded-xl border border-border bg-card/60 glass text-center space-y-3">
      <div className="h-10 w-10 rounded-full bg-primary/10 border border-primary/40 flex items-center justify-center text-primary mx-auto">
        <Lock className="h-5 w-5" />
      </div>
      <div className="font-semibold text-foreground text-sm uppercase">
        {title}
      </div>
      <p className="text-xs text-muted-foreground max-w-md mx-auto">
        {description}
      </p>
      <button
        onClick={onOpenLogin}
        className="px-4 py-2 bg-primary text-primary-foreground font-semibold text-xs rounded-lg hover:bg-primary/90 transition-all inline-flex items-center gap-1.5"
      >
        <Lock className="h-3.5 w-3.5" />
        <span>{buttonText}</span>
      </button>
    </div>
  );
};
