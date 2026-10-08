# Security Policy

## Reporting a vulnerability

Please report security issues privately to the maintainer (TaiWoo_Chen) instead of
opening a public issue. Include a description, the TaiPlan version, your Windows version,
and reproduction steps. Please do not include API keys, task contents or personal data in
your report.

## What TaiPlan stores, and where

| Data | Location | Notes |
| --- | --- | --- |
| Tasks, reminders, settings | `%LOCALAPPDATA%\TaiPlan` | Local database and JSON configuration files |
| Logs | `%LOCALAPPDATA%\TaiPlan\logs` | Runtime diagnostics; no credentials |
| API key | Windows Credential Manager | Service name `TaiPlan-AI`; never written to a file or a log |

TaiPlan has no account system, no sync service and no telemetry.

## Network access

TaiPlan only makes network requests when the optional AI feature is enabled, and only to
the provider, endpoint and model that the user configured. With AI disabled, no task
content leaves the machine.

When AI is enabled, the text submitted for parsing is sent to that provider. API keys are
sent only in the authentication header of those requests by the provider client.

## Local security posture

- The AI API key is stored in Windows Credential Manager, not in a configuration file.
- API keys and secrets are never written to logs, backups or the notifications list.
- The application data directory is per-user; no administrator rights are required to
  install or to run.
- The installed program directory is treated as read-only; all writes go to the user data
  directory.
- A single-instance lock and a local IPC channel prevent two copies of TaiPlan from
  writing to the same database at once.
- Uninstalling does not delete user data, so an accidental uninstall cannot destroy tasks.

## Known limitations

- Release builds for 0.1.2 are **not code-signed**, so Windows SmartScreen may warn on
  first run.
- The AI feature trusts the provider the user configures; TaiPlan does not proxy or
  re-encrypt that traffic.
