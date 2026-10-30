const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface ApiOptions {
  token?: string;
}

async function apiFetch<T>(
  endpoint: string,
  options: RequestInit & ApiOptions = {}
): Promise<T> {
  const { token, ...fetchOptions } = options;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(fetchOptions.headers as Record<string, string>),
  };

  // If token is not explicitly passed, try to fetch from Supabase localStorage
  let authToken = token;
  if (!authToken && typeof window !== "undefined") {
    try {
      const stored = localStorage.getItem("agentpay_token");
      if (stored) {
        authToken = stored;
      }
    } catch {}
  }

  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }

  // Ensure endpoint starts with /api if not present
  const fullEndpoint = endpoint.startsWith("/api") ? endpoint : `/api${endpoint}`;

  const response = await fetch(`${API_URL}${fullEndpoint}`, {
    ...fetchOptions,
    headers,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(
      errorData.detail || errorData.message || `API error: ${response.status}`
    );
  }

  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}

// ----------------------------------------------------------------------
// Auth
// ----------------------------------------------------------------------

export interface AuthResponse {
  access_token: string;
  refresh_token?: string;
  user: {
    id: string;
    email: string;
    full_name?: string;
  };
}

export async function login(email: string, password: string): Promise<AuthResponse> {
  const res = await apiFetch<AuthResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  if (res?.access_token && typeof window !== "undefined") {
    localStorage.setItem("agentpay_token", res.access_token);
  }
  return res;
}

export async function signup(email: string, password: string, fullName?: string): Promise<AuthResponse> {
  const res = await apiFetch<AuthResponse>("/auth/signup", {
    method: "POST",
    body: JSON.stringify({ email, password, full_name: fullName }),
  });
  if (res?.access_token && res.access_token !== "verification-required" && typeof window !== "undefined") {
    localStorage.setItem("agentpay_token", res.access_token);
  }
  return res;
}

export async function logout(token?: string) {
  if (typeof window !== "undefined") {
    localStorage.removeItem("agentpay_token");
  }
  return apiFetch("/auth/logout", {
    method: "POST",
    token,
  });
}

export async function getMe(token?: string) {
  return apiFetch<{ user: any; profile: any }>("/auth/me", { token });
}

// ----------------------------------------------------------------------
// Mandates
// ----------------------------------------------------------------------

export interface MandateConstraints {
  max_price?: number;
  currency?: string;
  category?: string;
  deadline?: string;
  auto_execute?: boolean;
  additional_rules?: string;
}

export interface Mandate {
  id: string;
  user_id: string;
  description: string;
  payment_token_id: string;
  status:
    | "active"
    | "searching"
    | "evaluating"
    | "authorized"
    | "completed"
    | "cancelled"
    | "failed"
    | string;
  constraints?: MandateConstraints | Record<string, any>;
  agent_reasoning?: string;
  paypal_order_id?: string;
  paypal_authorization_id?: string;
  executed_amount?: number;
  created_at: string;
  updated_at: string;
}

export async function getMandates(token?: string): Promise<Mandate[]> {
  return apiFetch<Mandate[]>("/mandates", { token });
}

export async function getMandate(id: string, token?: string): Promise<Mandate> {
  return apiFetch<Mandate>(`/mandates/${id}`, { token });
}

export async function createMandate(
  data: {
    description: string;
    payment_token_id: string;
    constraints?: MandateConstraints;
  },
  token?: string
): Promise<Mandate> {
  return apiFetch<Mandate>("/mandates", {
    method: "POST",
    body: JSON.stringify(data),
    token,
  });
}

export async function cancelMandate(
  id: string,
  token?: string
): Promise<Mandate> {
  return apiFetch<Mandate>(`/mandates/${id}/cancel`, {
    method: "POST",
    token,
  });
}

// ----------------------------------------------------------------------
// Transactions
// ----------------------------------------------------------------------

export interface Transaction {
  id: string;
  mandate_id: string;
  user_id?: string;
  paypal_resource_id?: string;
  event_type: string;
  paypal_order_id?: string;
  paypal_authorization_id?: string;
  paypal_capture_id?: string;
  amount?: number;
  currency?: string;
  status?: string;
  details?: Record<string, any>;
  created_at: string;
}

export async function getMandateTransactions(
  mandateId: string,
  token?: string
): Promise<Transaction[]> {
  return apiFetch<Transaction[]>(`/mandates/${mandateId}/transactions`, {
    token,
  });
}

export async function getTransactions(token?: string): Promise<Transaction[]> {
  return apiFetch<Transaction[]>("/mandates/transactions/all", {
    token,
  });
}

// ----------------------------------------------------------------------
// Vault (Payment Methods)
// ----------------------------------------------------------------------

export interface VaultToken {
  id: string;
  user_id: string;
  paypal_token_id: string;
  payment_source_type: "paypal" | "card" | string;
  display_name: string;
  is_active: boolean;
  created_at: string;
}

export async function setupVault(
  returnUrl?: string,
  cancelUrl?: string,
  token?: string
): Promise<{ approval_url: string; setup_token_id: string }> {
  const currentOrigin = typeof window !== "undefined" ? window.location.origin : "http://localhost:3000";
  return apiFetch("/vault/setup", {
    method: "POST",
    body: JSON.stringify({
      return_url: returnUrl || `${currentOrigin}/dashboard/vault?success=true`,
      cancel_url: cancelUrl || `${currentOrigin}/dashboard/vault?cancelled=true`,
    }),
    token,
  });
}

export async function confirmVault(
  setupTokenId: string,
  token?: string
): Promise<{ message: string; token: VaultToken }> {
  return apiFetch("/vault/confirm", {
    method: "POST",
    body: JSON.stringify({ setup_token_id: setupTokenId }),
    token,
  });
}

export async function getVaultTokens(token?: string): Promise<VaultToken[]> {
  return apiFetch<VaultToken[]>("/vault/tokens", { token });
}

export async function deleteVaultToken(
  id: string,
  token?: string
): Promise<void> {
  return apiFetch<void>(`/vault/tokens/${id}`, {
    method: "DELETE",
    token,
  });
}
