/** v1 API client helpers (task 1.4.1). Legacy calls remain in `src/services/api.ts`. */

export const V1_BASE_URL = "http://127.0.0.1:8000/api/v1";

export interface V1HealthResponse {
  status: string;
  api_version: string;
}

export async function fetchV1Health(
  baseUrl: string = V1_BASE_URL,
): Promise<V1HealthResponse> {
  const response = await fetch(`${baseUrl}/health`);
  if (!response.ok) {
    throw new Error(`v1 health check failed: HTTP ${response.status}`);
  }
  return (await response.json()) as V1HealthResponse;
}
