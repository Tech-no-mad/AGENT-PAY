"use client";

import React from "react";
import Link from "next/link";
import { Zap } from "lucide-react";

export default function Navbar() {
  return (
    <nav className="fixed top-0 left-0 right-0 z-50 glass border-b border-gray-800">
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2 group">
          <div className="w-8 h-8 rounded-lg bg-primary-500 flex items-center justify-center group-hover:bg-primary-400 transition-colors">
            <Zap className="w-5 h-5 text-white" />
          </div>
          <span className="text-xl font-bold text-white">AgentPay</span>
        </Link>

        {/* Nav links */}
        <div className="hidden md:flex items-center gap-8">
          <a
            href="#features"
            className="text-gray-400 hover:text-gray-200 transition-colors text-sm"
          >
            Features
          </a>
          <a
            href="#how-it-works"
            className="text-gray-400 hover:text-gray-200 transition-colors text-sm"
          >
            How It Works
          </a>
        </div>

        {/* CTA */}
        <Link
          href="/dashboard"
          className="btn-primary text-sm py-2"
        >
          Dashboard
        </Link>
      </div>
    </nav>
  );
}
