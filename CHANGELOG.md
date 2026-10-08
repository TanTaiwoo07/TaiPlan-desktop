# Changelog

All notable changes to TaiPlan are documented in this file.
This project follows [Semantic Versioning](https://semver.org/).

## [0.1.2] - 2026-10-08

### Fixed

- **Theme switching on dark-mode systems**: choosing Light in the app while Windows uses
  dark mode left the title bar, native widgets (radio buttons, secondary buttons) and the
  calendar iframe dark. The user's theme choice is now passed to the Streamlit server as
  the theme base (components included), the desktop window background follows the saved
  theme, and a CSS fallback pins built-in widget colors so the UI stays readable even
  before a restart. Saving a theme whose base changed now offers "Restart TaiPlan now"
  (graceful restart via the existing single-instance shutdown path).

### Added

- Keyboard shortcut: `Ctrl+N` / `Cmd+N` opens the new-task dialog from the Today page.
  (Esc closes dialogs natively.)

## [0.1.1] - 2026-10-07

### Added

- **Calendar import (.ics)** under Settings -> Data: import events exported from other
  calendars as tasks (title, time, location, description, priority, and the recurrence
  rules that map cleanly). The database is backed up before importing; importing the same
  file twice does not create duplicates; recurrence rules that cannot be expressed are
  imported as single tasks and listed in the UI.
- The Data section (backup / restore / export / import) is reachable again in Settings.

### Fixed

- Fresh Windows machines could be blocked on first launch: startup traces written by the
  application itself were misread as a damaged data folder. The migration / fresh
  decision now runs before any write to the data folder, and a program-created empty
  database is no longer treated as user data. An unmarked database that contains real
  user rows is still refused, as before.
- Hover tooltips on the sidebar navigation could stay on screen and stack when the pointer
  left the window. The navigation no longer uses them; labels are still shown, including
  the hover reveal in rail mode.

## [0.1.0] - 2026-10-07

The first public release of TaiPlan (Windows desktop edition).

### Added

- **Today** view: timed tasks, all-day items and overdue work for the current day.
- **Inbox**: a holding area for tasks without a date.
- **Calendar**: month / week / day views with drag-to-reschedule, drag-to-resize,
  click-or-drag-to-create, in-place editing, a current-time indicator and a configurable
  time grid.
- **Time blocking**: all-day and timed items can be converted into each other by
  dragging; overlaps are warned about but allowed.
- **Recurrence**: daily, weekly, monthly and custom-interval series, weekday-only series,
  per-occurrence edits, and per-occurrence or per-series deletion.
- **Reminders**: time-based reminders, Windows system notifications, and a reminder
  center listing recent and upcoming reminders.
- **Optional AI parsing**: natural-language input turned into structured tasks, using a
  provider, endpoint, model and API key chosen by the user. Off by default.
- **Chinese and English UI** (zh-CN / en-US), following the system language by default,
  with localized date and time formatting.
- **Light and dark appearance**, collapsible sidebar and a Fluent-inspired design.
- **Settings** as a single continuous scrolling page with an on-page table of contents.
- **Local-first storage**: a local database in the user profile, plus manual and
  automatic backups and a safe restore path.
- **Data directory migration**: existing users of the previous product name are migrated
  automatically on first start, with the old directory preserved as a fallback.
- **Windows installer**: per-user installation, no administrator rights, Start Menu entry,
  and uninstall that never removes user data.
- **Packaging**: signed-off release pipeline producing a single-file installer with a
  SHA256 sidecar, and a third-party notices file generated from the real bundle.

### Security

- AI API keys are stored in Windows Credential Manager, never in plaintext files, logs or
  backups.
- The application never writes into its own installation directory; user data, logs and
  state live in the per-user data directory.

### Known limitations

- The 0.1.0 release build is not code-signed yet.
- AI parsing quality depends on the provider and model the user configures.
