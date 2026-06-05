/**
 * Standard API error payload from the FastAPI sidecar (task 1.1.3).
 *
 * @see docs/API_ERROR_CODES.md
 */

export const API_ERROR_CODES = [
  "AI_CORE_FAILURE",
  "DB_LOCKED",
  "PATH_INVALID",
  "VALIDATION_ERROR",
  "NOT_FOUND",
  "INTERNAL_ERROR",
  "SCAN_IN_PROGRESS",
  "SCAN_CANCELLED",
] as const;

export type ApiErrorCode = (typeof API_ERROR_CODES)[number];

export interface ApiErrorBody {
  error: string;
  code: ApiErrorCode;
  hint?: string | null;
  details?: Record<string, unknown> | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isApiErrorCode(value: unknown): value is ApiErrorCode {
  return (
    typeof value === "string" &&
    (API_ERROR_CODES as readonly string[]).includes(value)
  );
}

/** Type guard for the unified backend error JSON shape. */
export function isApiError(value: unknown): value is ApiErrorBody {
  return (
    isRecord(value) &&
    typeof value.error === "string" &&
    isApiErrorCode(value.code)
  );
}

/**
 * Parse an HTTP error response body into {@link ApiErrorBody} when possible.
 * Falls back to legacy FastAPI ``detail`` strings.
 */
export function parseApiErrorBody(body: unknown): ApiErrorBody | null {
  if (isApiError(body)) {
    return body;
  }

  if (!isRecord(body)) {
    return null;
  }

  const detail = body.detail;

  if (typeof detail === "string") {
    return {
      error: detail,
      code: "INTERNAL_ERROR",
      hint: null,
      details: null,
    };
  }

  if (isApiError(detail)) {
    return detail;
  }

  return null;
}

/** User-facing message: prefer ``hint``, then ``error``. */
export function apiErrorMessage(body: ApiErrorBody): string {
  if (body.hint && body.hint.trim().length > 0) {
    return body.hint;
  }
  return body.error;
}
