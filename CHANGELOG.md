# 3.0.0-rc1

Professional control plane release candidate.

### Added
- Session authentication, roles, TOTP 2FA, scoped API tokens and encrypted secrets.
- Broadcast profiles, GPU capability detection and FFmpeg telemetry.
- Source failover, maintenance fallback, local recording, markers and clip generation.
- 24/7 broadcast grid, anti-repeat rotation, bumpers and commercial insertion.
- URL ingestion, automatic watch folder, scheduled retention and daily DB snapshots.
- PWA, NOC Wall, analytics, diagnostics and severity-based alerts.
- Multi-server agents, remote dispatch, load-aware placement and node failover.
- YouTube/Twitch optional API verification.

### Compatibility
- Existing v2 SQLite/channel migration remains in place.
- Persistent `data/`, `media/`, `logs/` and `.env` remain outside Git.
