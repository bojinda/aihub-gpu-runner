# Offline qualification setup

Run `python -B qualification/bootstrap_fixture.py` in a normal local Git checkout before `python -B validate_offline.py test-results/music-release.json`. The old-reader test loads the exact base core fixture, SHA-256 checked against deployed revision 4ebbc624. No production access is needed. The release source archive already includes this verified fixture for network-isolated WSL execution where the Windows worktree's Git metadata path is unavailable.

All new Music tests use fake transports, GPU observations and clocks, and private AtomicStore/physical lock files. The existing full suite includes loopback HTTP and local multiprocess crash tests. On Linux, run in `unshare -Urn` with only `ip link set lo up`; do not point tests at production config, locks or endpoints. The optional historical meeting launcher test requires a separate synthetic harness and is reported as skipped when absent.
