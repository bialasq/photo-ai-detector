import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { V1_BASE_URL } from "@/api/client";
import { ErrorBoundary } from "@/components/ErrorBoundary";

function Thrower(): JSX.Element {
  throw new Error("boom");
}

describe("ErrorBoundary", () => {
  it("renders fallback when a child throws", () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ErrorBoundary scope="test">
        <Thrower />
      </ErrorBoundary>,
    );

    expect(
      screen.getByText(/Something broke in test\. Try again or restart the app\./),
    ).toBeInTheDocument();

    expect(fetchMock).toHaveBeenCalledWith(
      `${V1_BASE_URL}/log-error`,
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const payload = JSON.parse(String(init.body)) as {
      scope: string;
      message: string;
      stack: string;
    };
    expect(payload.scope).toBe("test");
    expect(payload.message).toBe("boom");
    expect(typeof payload.stack).toBe("string");
    expect(payload.stack.length).toBeGreaterThan(0);
  });
});
