/** Scan API helpers (task 1.2.3, 1.4.3). */

import { V1_BASE_URL } from "@/api/client";

export async function cancelScan(): Promise<void> {
  const response = await fetch(`${V1_BASE_URL}/scan-cancel`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`Scan cancel failed: HTTP ${response.status}`);
  }
}
