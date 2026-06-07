import { describe, expect, it } from "vitest";
import {
  apiErrorMessage,
  isApiError,
  parseApiErrorBody,
} from "@/api/errors";

describe("api error parsing", () => {
  it("isApiError accepts unified backend error payloads", () => {
    expect(
      isApiError({
        error: "Validation failed",
        code: "VALIDATION_ERROR",
        hint: "Check parameters",
      }),
    ).toBe(true);
    expect(isApiError({ detail: "legacy" })).toBe(false);
  });

  it("parseApiErrorBody returns unified errors unchanged", () => {
    const body = {
      error: "Scan in progress",
      code: "SCAN_IN_PROGRESS" as const,
      hint: "Wait for the current scan to finish.",
    };
    expect(parseApiErrorBody(body)).toEqual(body);
  });

  it("parseApiErrorBody maps FastAPI string detail to INTERNAL_ERROR", () => {
    expect(parseApiErrorBody({ detail: "Something went wrong" })).toEqual({
      error: "Something went wrong",
      code: "INTERNAL_ERROR",
      hint: null,
      details: null,
    });
  });

  it("apiErrorMessage prefers hint over error text", () => {
    expect(
      apiErrorMessage({
        error: "Path invalid",
        code: "PATH_INVALID",
        hint: "Choose an existing photo directory.",
      }),
    ).toBe("Choose an existing photo directory.");

    expect(
      apiErrorMessage({
        error: "Not found",
        code: "NOT_FOUND",
        hint: "   ",
      }),
    ).toBe("Not found");
  });
});
