"use client";

import React from "react";

interface StatusBadgeProps {
  status: string;
  size?: "sm" | "md";
}

const statusConfig: Record<
  string,
  { color: string; bg: string; dot: string; pulse: boolean }
> = {
  active: {
    color: "text-blue-400",
    bg: "bg-blue-500/10 border-blue-500/20",
    dot: "bg-blue-400",
    pulse: true,
  },
  searching: {
    color: "text-yellow-400",
    bg: "bg-yellow-500/10 border-yellow-500/20",
    dot: "bg-yellow-400",
    pulse: true,
  },
  evaluating: {
    color: "text-orange-400",
    bg: "bg-orange-500/10 border-orange-500/20",
    dot: "bg-orange-400",
    pulse: true,
  },
  authorized: {
    color: "text-purple-400",
    bg: "bg-purple-500/10 border-purple-500/20",
    dot: "bg-purple-400",
    pulse: true,
  },
  completed: {
    color: "text-green-400",
    bg: "bg-green-500/10 border-green-500/20",
    dot: "bg-green-400",
    pulse: false,
  },
  cancelled: {
    color: "text-gray-400",
    bg: "bg-gray-500/10 border-gray-500/20",
    dot: "bg-gray-400",
    pulse: false,
  },
  failed: {
    color: "text-red-400",
    bg: "bg-red-500/10 border-red-500/20",
    dot: "bg-red-400",
    pulse: false,
  },
};

export default function StatusBadge({ status, size = "md" }: StatusBadgeProps) {
  const config = statusConfig[status.toLowerCase()] || statusConfig.active;
  const sizeClasses =
    size === "sm" ? "text-xs px-2 py-0.5" : "text-sm px-3 py-1";

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border font-medium capitalize ${config.bg} ${config.color} ${sizeClasses}`}
    >
      <span className="relative flex h-2 w-2">
        {config.pulse && (
          <span
            className={`absolute inline-flex h-full w-full animate-status-pulse rounded-full ${config.dot} opacity-75`}
          />
        )}
        <span
          className={`relative inline-flex h-2 w-2 rounded-full ${config.dot}`}
        />
      </span>
      {status}
    </span>
  );
}
