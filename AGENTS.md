# Fork-Specific Instructions

This repository is `marcmy/libtorrent`, the engine half of a coordinated custom qBittorrent + libtorrent project.

Read [FORK.md] before changing engine behavior. The canonical working baseline is `RC_2_1`, consumed by `marcmy/qBittorrent:master` or a matching feature branch.

Before implementing a feature or fix, determine whether the root behavior belongs to libtorrent, qBittorrent, or both. Engine and protocol behavior belongs here; GUI, application preferences, packaging and presentation belong in qBittorrent.

For coordinated work, use the same branch slug in both repositories and record the exact counterpart branch or commit. The consuming qBittorrent branch must be built against the exact libtorrent commit under test.

Treat disk I/O, hashing, resume data, storage movement, tracker state, piece selection, networking and protocol changes as high-risk. Add focused tests and consider data-corruption and compatibility risks before performance or style concerns.

Internal branches and same-fork pull requests in `marcmy/libtorrent` may be created when the repository owner explicitly requests repository changes. Do not open, comment on, or otherwise engage with upstream/community issues or pull requests on the user's behalf.

## Branch roles

- `RC_2_1`: normal base for this project and the default branch
- `RC_2_0`: compatibility research and deliberate backports only
- `master`: future-development comparison and integration only

Do not silently retarget routine qBittorrent builds away from `RC_2_1`.

## Review priorities

1. Correctness and data integrity
2. Protocol compatibility and observable behavior
3. Concurrency, lifetime and error handling
4. Performance and resource usage
5. Maintainability and style

Prefer a generally useful engine capability over qBittorrent-specific policy. When qBittorrent needs control or visibility, expose the smallest appropriate setting, command, status field or alert and implement the application policy in qBittorrent.

[FORK.md]: FORK.md
