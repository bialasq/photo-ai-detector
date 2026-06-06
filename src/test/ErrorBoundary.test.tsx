import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "@/components/ErrorBoundary";

function Thrower(): JSX.Element {
  throw new Error("boom");
}

describe("ErrorBoundary", () => {
  it("renders fallback when a child throws", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 204,
      }),
    );

    render(
      <ErrorBoundary scope="test">
        <Thrower />
      </ErrorBoundary>,
    );

    expect(
      screen.getByText(/Something broke in test\. Try again or restart the app\./),
    ).toBeInTheDocument();
  });
});
