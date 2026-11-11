#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -B -m unittest tests.test_audit tests.test_idle_recovery tests.test_idle_prerequisite tests.test_ollama_readonly_probe tests.test_ollama_census_exit tests.test_ollama_exit_races tests.test_ollama_child_settling tests.test_ollama_utilization_settling tests.test_native_lock tests.test_physical_lock_identity tests.test_ha_recovery tests.test_ha_boundaries
