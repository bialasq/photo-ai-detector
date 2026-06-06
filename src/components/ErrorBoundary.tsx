import type { ErrorInfo, ReactNode } from "react";
import { Component } from "react";
import { V1_BASE_URL } from "@/api/client";

export interface ErrorBoundaryProps {
  scope: string;
  fallback?: ReactNode;
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

function defaultFallback(scope: string): ReactNode {
  return (
    <div
      role="alert"
      className="flex min-h-[12rem] flex-col items-center justify-center gap-3 rounded-xl border border-red-200 bg-red-50 p-8 text-center"
    >
      <p className="text-base font-semibold text-red-800">
        Something broke in {scope}. Try again or restart the app.
      </p>
    </div>
  );
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = {
    error: null,
    errorInfo: null,
  };

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    this.setState({ errorInfo });

    void fetch(`${V1_BASE_URL}/log-error`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scope: this.props.scope,
        message: error.message,
        stack: errorInfo.componentStack,
      }),
    }).catch(() => {
      // Logging must not disturb the fallback UI.
    });
  }

  render(): ReactNode {
    const { error } = this.state;
    if (error !== null) {
      return this.props.fallback ?? defaultFallback(this.props.scope);
    }
    return this.props.children;
  }
}
