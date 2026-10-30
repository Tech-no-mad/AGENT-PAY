"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Shield, Mail, Lock, ArrowRight, AlertCircle, Sparkles } from "lucide-react";
import { login, signup } from "@/lib/api";
import { supabase } from "@/lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const [isLogin, setIsLogin] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      if (isLogin) {
        // Try backend auth API
        try {
          const res = await login(email, password);
          if (res?.access_token) {
            localStorage.setItem("agentpay_token", res.access_token);
            localStorage.setItem("agentpay_email", email);
            router.push("/dashboard");
            return;
          }
        } catch (apiErr: any) {
          // If supabase client is directly configured, try client-side auth
          if (supabase) {
            const { data, error: authError } = await supabase.auth.signInWithPassword({
              email,
              password,
            });
            if (authError) throw authError;
            if (data.session) {
              localStorage.setItem("agentpay_token", data.session.access_token);
              localStorage.setItem("agentpay_email", email);
              router.push("/dashboard");
              return;
            }
          }
          throw apiErr;
        }
      } else {
        // Sign up
        try {
          const res = await signup(email, password, fullName);
          if (res?.access_token && res.access_token !== "verification-required") {
            localStorage.setItem("agentpay_token", res.access_token);
            localStorage.setItem("agentpay_email", email);
            router.push("/dashboard");
            return;
          }
          setSuccess("Account created! You can now log in.");
          setIsLogin(true);
        } catch (apiErr: any) {
          if (supabase) {
            const { data, error: authError } = await supabase.auth.signUp({
              email,
              password,
              options: { data: { full_name: fullName } },
            });
            if (authError) throw authError;
            if (data.session) {
              localStorage.setItem("agentpay_token", data.session.access_token);
              localStorage.setItem("agentpay_email", email);
              router.push("/dashboard");
              return;
            } else {
              setSuccess("Verification email sent! Check your inbox.");
              setIsLogin(true);
              return;
            }
          }
          throw apiErr;
        }
      }
    } catch (err: any) {
      setError(err.message || "An unexpected error occurred");
    } finally {
      setLoading(false);
    }
  };

  const handleDemoLogin = () => {
    localStorage.setItem("agentpay_token", "demo-token-" + Date.now());
    localStorage.setItem("agentpay_email", "demo@agentpay.io");
    router.push("/dashboard");
  };

  return (
    <div className="min-h-screen flex items-center justify-center px-6 py-12 bg-gray-950">
      <div className="w-full max-w-md space-y-8">
        {/* Logo & Header */}
        <div className="text-center space-y-2">
          <Link href="/" className="inline-flex items-center gap-2 mb-2">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-primary-600 to-sky-400 flex items-center justify-center shadow-lg shadow-primary-500/20">
              <Shield className="w-6 h-6 text-white" />
            </div>
          </Link>
          <h1 className="text-2xl font-bold text-gray-100">
            {isLogin ? "Sign in to AgentPay" : "Create your account"}
          </h1>
          <p className="text-xs text-gray-400">
            {isLogin
              ? "Access your autonomous mandates and PayPal spending rails"
              : "Start delegating purchases safely with policy-guarded AI agents"}
          </p>
        </div>

        {/* Form Container */}
        <div className="p-8 rounded-2xl border border-gray-800 bg-gray-900/60 backdrop-blur-xl shadow-2xl space-y-6">
          {/* Notifications */}
          {error && (
            <div className="p-3 rounded-xl border border-red-500/20 bg-red-500/10 text-red-400 text-xs flex items-start gap-2">
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}

          {success && (
            <div className="p-3 rounded-xl border border-green-500/20 bg-green-500/10 text-green-400 text-xs">
              {success}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            {!isLogin && (
              <div>
                <label className="block text-xs font-medium text-gray-300 mb-1.5">
                  Full Name
                </label>
                <input
                  type="text"
                  required
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="John Doe"
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3.5 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-primary-500"
                />
              </div>
            )}

            <div>
              <label className="block text-xs font-medium text-gray-300 mb-1.5">
                Email Address
              </label>
              <div className="relative">
                <Mail className="w-4 h-4 text-gray-500 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg pl-9 pr-3.5 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-primary-500"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-300 mb-1.5">
                Password
              </label>
              <div className="relative">
                <Lock className="w-4 h-4 text-gray-500 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg pl-9 pr-3.5 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-primary-500"
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg bg-primary-600 hover:bg-primary-500 disabled:opacity-50 text-white font-medium text-sm shadow-lg shadow-primary-600/20 transition-all mt-2"
            >
              {loading ? (
                <span>Please wait...</span>
              ) : (
                <>
                  <span>{isLogin ? "Sign In" : "Create Account"}</span>
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>

          {/* Toggle between Login and Sign Up */}
          <div className="text-center text-xs text-gray-400 pt-2 border-t border-gray-800/80">
            {isLogin ? "Don't have an account? " : "Already registered? "}
            <button
              type="button"
              onClick={() => {
                setIsLogin(!isLogin);
                setError(null);
                setSuccess(null);
              }}
              className="text-primary-400 hover:text-primary-300 font-medium underline"
            >
              {isLogin ? "Sign up" : "Sign in"}
            </button>
          </div>

          {/* Demo Sandbox Button */}
          <div className="pt-2">
            <button
              type="button"
              onClick={handleDemoLogin}
              className="w-full flex items-center justify-center gap-2 py-2 rounded-lg border border-dashed border-gray-700 bg-gray-800/40 text-gray-300 hover:bg-gray-800 text-xs transition-colors"
            >
              <Sparkles className="w-3.5 h-3.5 text-primary-400" />
              <span>Continue with Hackathon Sandbox Demo Mode</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
