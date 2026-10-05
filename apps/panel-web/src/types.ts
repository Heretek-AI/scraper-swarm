export interface ServiceResource {
  cpus?: number;
  mem?: string;
  shm?: string | null;
  pids?: number;
}

export interface ServiceItem {
  id: string;
  name: string;
  tier: "core" | "support" | "advanced" | "optional";
  license: { spdx: string; copyleft?: boolean };
  verified?: boolean;
  requires?: string[];
  params_schema?: {
    type: string;
    properties: Record<string, any>;
    required?: string[];
  };
  resources?: {
    lite?: ServiceResource;
    standard?: ServiceResource;
    heavy?: ServiceResource;
  };
  native_exposable?: boolean;
}

export interface ContainerStatus {
  ID?: string;
  Name?: string;
  Service?: string;
  Image?: string;
  State?: string;
  Status?: string;
  Ports?: string;
}

export interface InstalledServiceInfo {
  service_id: string;
  profile: string;
  status: string;
  params: Record<string, any>;
  updated_at?: string;
}

export interface SmokeTestResult {
  service_id: string;
  passed: boolean;
  latency_ms: number;
  checks: Array<{ name: string; passed: boolean; message: string }>;
  details?: Record<string, any>;
}

export interface SecurityPosture {
  score: number;
  master_key_secure: boolean;
  audit_chain_valid: boolean;
  admin_2fa_enforced: boolean;
  egress_default_deny: boolean;
  container_capabilities_dropped: boolean;
  details: string[];
}

export interface AuditEntry {
  id: number;
  timestamp: string;
  actor: string;
  action: string;
  target?: string;
  details?: string;
  prev_hash?: string;
  entry_hash?: string;
}

export interface AgentKey {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  rate_limit_rpm: number;
  created_at: string;
}
