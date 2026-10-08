# Contributing to TaiPlan

Thanks for your interest in TaiPlan. This project is a Windows desktop task and time
planning app built with Python, Streamlit and SQLite, packaged with PyInstaller and
Inno Setup.

## Ground rules

1. **No new runtime dependencies without a reason.** Every third-party package that ends
   up in the bundle has to be listed in `THIRD_PARTY_NOTICES.txt` and must survive the
   packaging size guard. Adding a heavy library for a small convenience is not accepted.
2. **`database.py` is the only module that may contain SQL.** The UI layer never writes
   SQL, and business rules live in `services.py`.
3. **Every user-visible string goes through `t("...")`.** No hardcoded Chinese or English
   text in the UI, and no `if language == ...` branches. Add new keys to both
   `i18n/zh_CN.py` and `i18n/en_US.py`.
4. **CSS may only hook onto stable class names** (our own `st-key-*` / our own classes).
   Streamlit's generated hashed class names are off limits, and there is no global
   `button { }` styling.
5. **Never break someone's data.** Schema changes must be additive and re-runnable; the
   data directory migration in `data_dir_migration.py` is copy-first and never deletes or
   modifies the legacy directory.
6. **Tests must be hermetic.** Tests use temporary databases and temporary data
   directories. They must never read or write the real user profile data directory.
7. **Do not change packaging identity casually.** The installer AppId GUID, the App User
   Model ID, the credential service name and the executable name are all part of the
   upgrade contract.

## Getting started

```bat
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run app.py
```

## Tests

```bat
:: everything
.venv\Scripts\python.exe -m unittest discover -p "test_*.py"

:: a single module
.venv\Scripts\python.exe -m unittest test_services
```

Please keep the suite green. A change that makes a test weaker (skipping, deleting
assertions, or turning a failure into a warning) is not a fix.

Areas covered by tests that should stay covered: product identity, internationalization,
the continuous Settings page, the data directory migration, credential fallback, the
single-instance lock and IPC, the parent watchdog, graceful shutdown, and the frozen
runtime dependencies.

## Layout

| Path | Purpose |
| --- | --- |
| `app.py` | Streamlit page (UI composition only) |
| `main.py` | entry point for every run mode |
| `desktop_runtime.py` | native window, tray, lock, IPC, watchdog |
| `services.py` | business logic |
| `database.py` | schema and SQL |
| `ui/` | theme, layout, components, icons |
| `i18n/` | translations and date localization |
| `calendar_view.py`, `recurrence.py` | calendar and recurrence |
| `notification_service.py`, `reminder_worker.py` | reminders |
| `ai_client.py`, `ai_settings.py` | optional AI parsing and key storage |
| `data_dir_migration.py`, `data_migration.py` | data directory and legacy migration |
| `packaging_tools.py`, `TaiPlan.spec`, `build*.py` | packaging |
| `installer/TaiPlan.iss` | Inno Setup script |

## Building

```bat
.venv\Scripts\python.exe build.py            :: dist\TaiPlan\TaiPlan.exe (onedir)
.venv\Scripts\python.exe build_installer.py  :: release\TaiPlan-Setup-<version>.exe
.venv\Scripts\python.exe build_release.py    :: full pipeline
```

The full pipeline runs: executable build, frozen smoke test, sensitive-file and path
scan, installer input scan, Inno Setup compile, SHA256 generation and verification.

Do not hand-edit generated artifacts (`dist/`, `release/`, `build/`,
`THIRD_PARTY_NOTICES.txt`).

## Reporting issues

Please include the TaiPlan version, your Windows version, and what you did. If the app
misbehaves, the log files in `%LOCALAPPDATA%\TaiPlan\logs` are usually the fastest way to
see what happened. Never paste an API key into an issue.
