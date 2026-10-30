"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutDashboard,
  FileText,
  CreditCard,
  History,
  LogOut,
  Plus,
  Shield,
  Menu,
  X,
  User,
} from "lucide-react";
import { logout } from "@/lib/api";

const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/dashboard/mandates", label: "Mandates", icon: FileText },
  { href: "/dashboard/vault", label: "Payment Methods", icon: CreditCard },
  { href: "/dashboard/history", label: "Audit History", icon: History },
];

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [userEmail, setUserEmail] = useState<string>("User");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("agentpay_token");
    if (!token) {
      router.push("/login");
      return;
    }

    // Try reading user info from localStorage or token
    try {
      const storedEmail = localStorage.getItem("agentpay_email");
      if (storedEmail) {
        setUserEmail(storedEmail);
      }
    } catch {}

    setLoading(false);
  }, [router]);

  const handleLogout = async () => {
    try {
      await logout();
    } catch {}
    localStorage.removeItem("agentpay_token");
    localStorage.removeItem("agentpay_email");
    router.push("/login");
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <div className="flex items-center gap-3 text-gray-400">
          <div className="w-5 h-5 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
          <span>Loading AgentPay...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex">
      {/* Desktop Sidebar */}
      <aside className="hidden md:flex flex-col w-64 border-r border-gray-800 bg-gray-900/50 backdrop-blur-sm">
        {/* Logo */}
        <div className="p-6 border-b border-gray-800 flex items-center justify-between">
          <Link href="/dashboard" className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-primary-600 to-sky-400 flex items-center justify-center shadow-lg shadow-primary-500/20">
              <Shield className="w-5 h-5 text-white" />
            </div>
            <div>
              <span className="font-bold text-lg bg-gradient-to-r from-white via-gray-200 to-gray-400 bg-clip-text text-transparent">
                AgentPay
              </span>
              <span className="block text-[10px] text-primary-400 font-mono tracking-wider">
                PAYPAL AI RAIL
              </span>
            </div>
          </Link>
        </div>

        {/* Action Button */}
        <div className="p-4">
          <Link
            href="/dashboard/mandates/new"
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-primary-600 hover:bg-primary-500 text-white font-medium shadow-md shadow-primary-600/20 transition-all text-sm"
          >
            <Plus className="w-4 h-4" />
            <span>Create Mandate</span>
          </Link>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 py-2 space-y-1">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive =
              pathname === item.href ||
              (item.href !== "/dashboard" && pathname.startsWith(item.href));
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-3 px-3.5 py-2.5 rounded-lg text-sm font-medium transition-all ${
                  isActive
                    ? "bg-primary-500/10 text-primary-400 border border-primary-500/20"
                    : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/50"
                }`}
              >
                <Icon className="w-4 h-4" />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        {/* User & Logout */}
        <div className="p-4 border-t border-gray-800 space-y-2">
          <div className="flex items-center gap-2.5 px-3 py-2 rounded-lg bg-gray-800/40 text-xs text-gray-400">
            <User className="w-4 h-4 text-primary-400" />
            <span className="truncate">{userEmail}</span>
          </div>
          <button
            onClick={handleLogout}
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs font-medium text-gray-400 hover:text-red-400 hover:bg-red-500/10 transition-colors"
          >
            <LogOut className="w-4 h-4" />
            <span>Sign Out</span>
          </button>
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Mobile Topbar */}
        <header className="md:hidden flex items-center justify-between p-4 border-b border-gray-800 bg-gray-900/50">
          <Link href="/dashboard" className="flex items-center gap-2">
            <Shield className="w-5 h-5 text-primary-400" />
            <span className="font-bold text-gray-100">AgentPay</span>
          </Link>
          <button
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="p-2 text-gray-400 hover:text-gray-200"
          >
            {mobileMenuOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
          </button>
        </header>

        {/* Mobile Navigation Drawer */}
        {mobileMenuOpen && (
          <div className="md:hidden border-b border-gray-800 bg-gray-900 px-4 py-3 space-y-2">
            <Link
              href="/dashboard/mandates/new"
              onClick={() => setMobileMenuOpen(false)}
              className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary-600 text-white text-sm font-medium mb-3"
            >
              <Plus className="w-4 h-4" />
              <span>Create Mandate</span>
            </Link>
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setMobileMenuOpen(false)}
                  className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-gray-300 hover:bg-gray-800"
                >
                  <Icon className="w-4 h-4" />
                  <span>{item.label}</span>
                </Link>
              );
            })}
            <button
              onClick={handleLogout}
              className="w-full flex items-center gap-3 px-3 py-2 text-sm text-red-400 hover:bg-red-500/10 rounded-lg pt-3 border-t border-gray-800"
            >
              <LogOut className="w-4 h-4" />
              <span>Sign Out</span>
            </button>
          </div>
        )}

        {/* Page Content */}
        <main className="flex-1 p-6 md:p-8 overflow-y-auto">
          {children}
        </main>
      </div>
    </div>
  );
}
