"""Read-only adapter over the retained RecoveryObserver.

Host collectors are deliberately injected: there is no permissive Linux
fallback, old incident helper import, HA-supplied evidence, or auto-install.
Deployment must qualify a collector against the permanent ingress perimeter.
"""
import time
from .recovery_checks import digest, generation

COVERAGE = frozenset({'ipv4', 'ipv6', 'loopback', 'bridge', 'direct_ip',
    'published_ports', 'established_connections', 'manual_comfyui',
    'docker_control', 'local_gpu_exec'})


class IsolationVerifier:
    """Compare independent host readbacks with a fixed reviewed inventory.

    Each test path has captured identity and positive deny evidence. Connection
    refusal, an HA pause, or an empty queue cannot prove access exclusion.
    Kernel readback excludes only volatile counters/handles in the collector.
    The two UIDs are constrained by root-owned units and fixed upstreams.
    """
    def __init__(self, collector, manifest, *, clock=time.time):
        if (set(manifest.get('paths', {})) != COVERAGE or
                not manifest.get('kernel_sha256') or not manifest.get('access_sha256') or
                not manifest.get('observer_routes') or not manifest.get('gateway_generation')):
            raise ValueError('reviewed_complete_perimeter_manifest_required')
        self.collector, self.manifest, self.clock = collector, manifest, clock

    def verify(self, *, fenced):
        raw = self.collector.collect(fenced=fenced)
        if not 0 <= self.clock()-raw['at'] <= 10:
            raise ValueError('perimeter_readback_stale')
        m = self.manifest
        if (raw['kernel_sha256'] != m['kernel_sha256'] or raw['access_sha256'] != m['access_sha256'] or
                raw['gateway_generation'] != m['gateway_generation'] or
                raw['observer_routes'] != m['observer_routes'] or
                raw['unmanaged_clients'] != [] or set(raw['paths']) != COVERAGE):
            raise ValueError('perimeter_or_producer_inventory_changed')
        for name, expected in m['paths'].items():
            item = raw['paths'][name]
            if (item['identity'] != expected or item['enforced'] is not True or
                    item['positive_deny_evidence'] is not True):
                raise ValueError('producer_path_not_excluded_' + name)
        if raw['observer_readiness'] is not True:
            raise ValueError('observer_direct_route_unavailable')
        if fenced and (raw['gate_closed'] is not True or raw['gateway_denial_verified'] is not True):
            raise ValueError('gateway_fence_not_verified')
        return dict(verified=True, binding=digest(m), coverage={k: True for k in COVERAGE},
                    observer_route_verified=True, unmanaged_clients=[],
                    gate_closed=raw['gate_closed'], denial_verified=raw['gateway_denial_verified'])


class ReadOnlyObserver:
    def __init__(self, existing_observer, identity_collector, isolation, *, source_digest,
                 configuration_digest, boot_reader, clock=time.time):
        self.existing, self.identities, self.isolation = existing_observer, identity_collector, isolation
        self.source, self.configuration, self.boot = source_digest, configuration_digest, boot_reader
        self.clock = clock

    def observe(self, *, fenced):
        deadline = time.monotonic() + min(30, self.existing.p['observation_timeout'])
        exclusion = self.isolation.verify(fenced=fenced)
        before = self.identities.capture(deadline)  # Full State except dynamic Health,
        # kernel PID birth, executable, netns inode, contract, IP/namespace route.
        proof = self.existing.stopped_ready(('gpu0', 'gpu1'), deadline, processes={})
        after = self.identities.capture(deadline)
        if ({k: generation(v) for k, v in before['containers'].items()} !=
                {k: generation(v) for k, v in after['containers'].items()}):
            raise ValueError('container_generation_changed_during_observation')
        again = self.isolation.verify(fenced=fenced)
        if exclusion != again:
            raise ValueError('exclusion_changed_during_observation')
        return dict(at=self.clock(), boot=self.boot(), source=self.source(),
                    configuration=self.configuration(), isolation=again, proof=proof,
                    backend_available=True, manual_activity=after['manual_activity'],
                    containers=after['containers'])


class UnavailableObserver:
    """Safe default until the platform-specific collector has been qualified."""
    def observe(self, *, fenced):
        raise RuntimeError('permanent_ingress_collector_not_qualified')
