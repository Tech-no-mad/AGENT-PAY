"use client";

import React from "react";
import Link from "next/link";
import { Shield, Search, CreditCard, ArrowRight, Zap } from "lucide-react";
import Navbar from "@/components/Navbar";

const features = [
  {
    icon: Shield,
    title: "Policy-Guarded",
    description:
      "Define spending mandates with natural language. Set budgets, categories, and constraints — your AI agent operates strictly within bounds.",
    color: "text-primary-400",
    bg: "bg-primary-500/10",
  },
  {
    icon: Search,
    title: "Autonomous Search",
    description:
      "AI agents search, compare, and evaluate products across the web to find the best deals that match your requirements.",
    color: "text-yellow-400",
    bg: "bg-yellow-500/10",
  },
  {
    icon: CreditCard,
    title: "Secure Payments",
    description:
      "Powered by PayPal's vault infrastructure. Tokenized payments ensure your financial data never touches the AI agent.",
    color: "text-green-400",
    bg: "bg-green-500/10",
  },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen">
      <Navbar />

      {/* Hero Section */}
      <section className="relative pt-32 pb-20 px-6">
        {/* Background gradient */}
        <div className="absolute inset-0 overflow-hidden">
          <div className="absolute top-1/4 left-1/2 -translate-x-1/2 w-[800px] h-[600px] bg-primary-500/10 rounded-full blur-[128px]" />
        </div>

        <div className="relative max-w-5xl mx-auto text-center">
          {/* Badge */}
          <div className="inline-flex items-center gap-2 bg-primary-500/10 border border-primary-500/20 rounded-full px-4 py-1.5 mb-8">
            <Zap className="w-4 h-4 text-primary-400" />
            <span className="text-sm text-primary-300 font-medium">
              PayPal AI Hackathon 2024
            </span>
          </div>

          <h1 className="text-5xl md:text-7xl font-bold mb-6 tracking-tight">
            <span className="text-white">Agent</span>
            <span className="gradient-text">Pay</span>
          </h1>

          <p className="text-xl md:text-2xl text-gray-400 mb-4 max-w-2xl mx-auto">
            Let AI agents handle your purchases — safely.
          </p>

          <p className="text-gray-500 mb-10 max-w-xl mx-auto">
            Define spending policies, connect your PayPal, and let autonomous AI
            agents find and buy what you need — all within your guardrails.
          </p>

          {/* CTA Buttons */}
          <div className="flex items-center justify-center gap-4">
            <Link href="/login" className="btn-primary text-lg px-8 py-3">
              Get Started
              <ArrowRight className="w-5 h-5" />
            </Link>
            <Link
              href="/dashboard"
              className="btn-secondary text-lg px-8 py-3"
            >
              View Dashboard
            </Link>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section id="features" className="py-20 px-6">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-3xl md:text-4xl font-bold text-white mb-4">
              How AgentPay Works
            </h2>
            <p className="text-gray-400 max-w-2xl mx-auto">
              Three layers of intelligence working together to make autonomous
              payments safe and effective.
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-6">
            {features.map((feature) => (
              <div
                key={feature.title}
                className="card-hover group text-center md:text-left"
              >
                <div
                  className={`w-12 h-12 rounded-xl ${feature.bg} flex items-center justify-center mb-4 mx-auto md:mx-0`}
                >
                  <feature.icon className={`w-6 h-6 ${feature.color}`} />
                </div>
                <h3 className="text-xl font-semibold text-white mb-2">
                  {feature.title}
                </h3>
                <p className="text-gray-400 text-sm leading-relaxed">
                  {feature.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-gray-800 py-8 px-6">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Zap className="w-5 h-5 text-primary-400" />
            <span className="font-semibold text-gray-400">AgentPay</span>
          </div>
          <p className="text-sm text-gray-600">
            Built for PayPal AI Hackathon 2024
          </p>
        </div>
      </footer>
    </div>
  );
}
