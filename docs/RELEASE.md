# Release & code signing (task 0.2)

> **Owner checklist (Luq):** Authenticode certificate procurement blocks Phase 4 MSI signing.

## Certificate procurement

| Step | Action | Status |
|------|--------|--------|
| 1 | Choose vendor (Sectigo OV, DigiCert, SSL.com, etc.) | ☐ |
| 2 | Complete organization / individual KYC verification | ☐ |
| 3 | Order **OV code signing** certificate (~$200–400/year) | ☐ |
| 4 | Receive PFX or USB hardware token (lead time **3–7 business days**) | ☐ |
| 5 | Store private key in 1Password / hardware token — **never in git** | ☐ |

## Signing MSI (Phase 4.1.2)

After `tauri build` produces the Windows installer:

```powershell
# Example — adjust paths to your PFX and timestamp server
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
  /f "C:\path\to\codesign.pfx" /p "***" `
  "D:\photo-ai-detector\src-tauri\target\release\bundle\msi\*.msi"
```

Verify:

```powershell
signtool verify /pa "path\to\installer.msi"
```

## Environment variables (CI / local)

| Variable | Purpose |
|----------|---------|
| `CODESIGN_PFX_PATH` | Path to `.pfx` (CI secret) |
| `CODESIGN_PFX_PASSWORD` | PFX password (CI secret) |

Do **not** commit certificates or passwords. GitHub Actions secrets only (task 4.1.4).

## Related tasks

- **4.1.2** — MSI Authenticode signing implementation
- **4.1.4** — Release workflow (tag → build → sign → upload)
- **4.5.1** — Clean Win11 VM install gate
