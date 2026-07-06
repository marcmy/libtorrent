# marcmy/libtorrent fork

This fork is the engine half of a coordinated qBittorrent + libtorrent project.

## Canonical baseline

- Working branch: `RC_2_1`
- Consumer repository: `marcmy/qBittorrent`
- Consumer baseline: `master`
- Routine custom qBittorrent builds must use this fork's `RC_2_1` branch or an explicitly named feature branch based on it.

`RC_2_0` is retained only for compatibility research or deliberate backports. `master` is retained for future-development comparison and integration work. Neither branch should silently replace `RC_2_1` in the daily-driver qBittorrent build.

## Ownership rule

Libtorrent owns engine and protocol behavior, including:

- peer connections and protocol state
- tracker announcing and scraping
- DHT, LSD, PEX and metadata exchange
- TCP, uTP, WebRTC and encryption internals
- NAT-PMP and UPnP engine behavior
- piece selection and hashing
- disk I/O and cache behavior
- rate limiting and scheduling internals
- torrent state transitions
- resume-data format and semantics

qBittorrent owns user-interface behavior, persisted application preferences, application policy, packaging and translation of engine alerts/state into user-visible behavior.

A coordinated change belongs in both repositories when this engine needs a new setting, command, status field or alert and qBittorrent must expose, persist or present it.

## Branch and integration method

1. Branch from `RC_2_1` for normal custom engine work.
2. Use the same branch slug in `marcmy/qBittorrent` when both repositories change.
3. Keep engine changes independently testable and avoid qBittorrent-specific policy inside libtorrent unless the capability is generally appropriate at the engine layer.
4. Record the exact qBittorrent counterpart branch or commit in coordinated work.
5. Build and test the consuming qBittorrent branch against the exact libtorrent commit under review.

## Compatibility and safety

Changes to disk I/O, resume data, hashing, tracker state, piece selection, networking or storage must receive focused regression coverage. Treat data-corruption, invalid resume-state and protocol-compatibility risks as release blockers.

Custom qBittorrent artifacts should record this repository's exact commit because the public version string alone does not uniquely identify a custom engine build.
