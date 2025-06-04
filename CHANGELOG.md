# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),  
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2025-06-04

### Changed

- Migrated WebSocket implementation from `websockets` to `aiohttp` for improved performance and compatibility.
- Centralized and standardized error response handling using `error_utils`.

### Breaking Changes ⚠️

- `client.close_connection()`, `client.transcribe_local_file()`, and `client.transcribe_remote_url()` no longer return values directly.  
  🔁 Instead, responses are sent via the event listener mechanism.
- Event system now supports **only one listener per event type**.  
  Registering a new listener for an event will **overwrite** the previous one.

---

## [0.1.0] - 2023-04-28

### Added

- Initial release of Bodhi Python SDK.
- Support for connecting to Bodhi WebSocket server.
