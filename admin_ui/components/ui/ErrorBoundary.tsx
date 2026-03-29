"use client";

import { Component, ReactNode } from "react";

type ErrorBoundaryProps = {
  children: ReactNode;
  fallback?: ReactNode;
};

type ErrorBoundaryState = {
  hasError: boolean;
};

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error) {
    console.error("Dashboard boundary caught error:", error);
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback ?? (
        <div className="glass-card rounded-3xl border border-rose-500/30 bg-rose-950/60 p-6 text-slate-100">
          <h3 className="text-lg font-semibold text-white">Something went wrong</h3>
          <p className="text-sm text-slate-300">
            We couldn’t load the dashboard. Please refresh or try again later.
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}
