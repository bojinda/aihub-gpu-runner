"""Docker inspect mount enumeration is not container configuration drift."""
import copy
import itertools
import json
import time
from types import SimpleNamespace
import unittest

from aihub_gpu_runner.core import RecoveryRequired
from aihub_gpu_runner.recovery import RecoveryObserver


MOUNTS = [
    {'Type': 'bind', 'Source': '/offline/output', 'Destination': '/app/output',
     'Mode': 'rw,z', 'RW': True, 'Propagation': 'rprivate'},
    {'Type': 'volume', 'Name': 'synthetic-cache', 'Driver': 'local',
     'Source': '/offline/volumes/cache/_data', 'Destination': '/app/cache',
     'Mode': 'rw', 'RW': True, 'Propagation': '',
     'ExtraOptions': {'labels': ['retained', 'in-order'], 'nested': {'enabled': True}}},
    {'Type': 'bind', 'Source': '/offline/models', 'Destination': '/app/models',
     'Mode': 'ro', 'RW': False, 'Propagation': 'rprivate'},
    {'Type': 'bind', 'Source': '/offline/extension', 'Destination': '/app/custom_nodes/gpu1',
     'Mode': 'ro', 'RW': False, 'Propagation': 'rprivate'},
]


def metadata(service='wallpaper', char='a'):
    name = {'wallpaper': 'comfyui-wallpaper', 'music': 'comfyui-music', 'ollama': 'ollama'}[service]
    return {'Id': char * 64, 'Name': '/' + name, 'Image': 'sha256:' + 'd' * 64,
        'Config': {'Hostname': char * 12, 'Image': 'synthetic:approved',
                   'Env': ['CUDA_VISIBLE_DEVICES=0,1'], 'Cmd': ['synthetic-server'],
                   'Labels': {'purpose': 'offline'}},
        'HostConfig': {'RestartPolicy': {'Name': 'no'}, 'NetworkMode': 'synthetic',
                       'DeviceRequests': [{'Driver': 'nvidia', 'DeviceIDs': ['GPU-0', 'GPU-1'],
                                           'Capabilities': [['gpu']]}]},
        'Mounts': copy.deepcopy(MOUNTS),
        'State': {'Running': False, 'Pid': 0, 'StartedAt': 'original',
                  'Paused': False, 'Dead': False, 'Restarting': False}}


class ContainerContractTests(unittest.TestCase):
    def setUp(self):
        self.meta = {s: metadata(s, c) for s, c in
                     (('wallpaper', 'a'), ('music', 'b'), ('ollama', 'c'))}
        self.calls = []
        services = {s: {'name': m['Name'][1:], 'id': m['Id'], 'image': m['Image'],
                       'contract_sha256': RecoveryObserver.contract(m),
                       'identity_mode': 'exact' if s == 'wallpaper' else 'contract'}
                    for s, m in self.meta.items()}
        self.observer = RecoveryObserver({'docker': '/offline/docker', 'services': services}, {}, {}, run=self.inspect)

    def inspect(self, argv, **kwargs):
        self.calls.append(argv)
        self.assertEqual(argv[:2], ['/offline/docker', 'inspect'], 'Read-only synthetic observations only')
        self.assertGreater(kwargs['timeout'], 0)
        matches = [m for m in self.meta.values() if argv[2] in (m['Id'], m['Name'][1:])]
        return SimpleNamespace(returncode=0, stdout=json.dumps(matches))

    def observed(self, service):
        return self.observer.container(service, time.monotonic() + 1)

    def test_all_mount_permutations_match_and_do_not_mutate_input(self):
        original = self.meta['wallpaper']
        expected = RecoveryObserver.contract(original)
        for mounts in itertools.permutations(MOUNTS):
            candidate = copy.deepcopy(original)
            candidate['Mounts'] = copy.deepcopy(list(mounts))
            before = copy.deepcopy(candidate)
            with self.subTest(destinations=[m['Destination'] for m in mounts]):
                self.assertEqual(RecoveryObserver.contract(candidate), expected)
                self.assertEqual(candidate, before)

    def test_every_mount_attribute_remains_bound(self):
        base = self.meta['wallpaper']
        expected = RecoveryObserver.contract(base)
        mutations = {'Source': '/offline/other-output', 'Destination': '/app/other-output',
                     'Type': 'volume', 'Mode': 'ro,z', 'RW': False, 'Propagation': 'rshared',
                     'NewAttribute': {'preserved': True}}
        for field, value in mutations.items():
            changed = copy.deepcopy(base)
            changed['Mounts'][0][field] = value
            with self.subTest(field=field):
                self.assertNotEqual(RecoveryObserver.contract(changed), expected)
        for field, value in {'Name': 'other-cache', 'Driver': 'other-driver',
                              'ExtraOptions': {'labels': ['in-order', 'retained'],
                                               'nested': {'enabled': False}}}.items():
            changed = copy.deepcopy(base)
            changed['Mounts'][1][field] = value
            with self.subTest(volume_field=field):
                self.assertNotEqual(RecoveryObserver.contract(changed), expected)

    def test_adding_or_removing_mounts_changes_contract(self):
        base = self.meta['wallpaper']
        expected = RecoveryObserver.contract(base)
        for count in range(len(MOUNTS)):
            changed = copy.deepcopy(base)
            changed['Mounts'] = changed['Mounts'][:count]
            with self.subTest(remaining=count):
                self.assertNotEqual(RecoveryObserver.contract(changed), expected)
        changed = copy.deepcopy(base)
        changed['Mounts'].append(dict(MOUNTS[0], Destination='/app/additional'))
        self.assertNotEqual(RecoveryObserver.contract(changed), expected)

    def test_invalid_mount_arrays_and_destinations_fail_closed(self):
        for mounts in (None, {}, 'invalid', [None], ['invalid'], [{}]):
            changed = copy.deepcopy(self.meta['wallpaper'])
            changed['Mounts'] = mounts
            with self.subTest(mounts=mounts), self.assertRaises(RecoveryRequired):
                RecoveryObserver.contract(changed)
        changed = copy.deepcopy(self.meta['wallpaper'])
        del changed['Mounts']
        with self.assertRaises(RecoveryRequired):
            RecoveryObserver.contract(changed)
        for destination in (None, '', 1, True, [], {}, 'relative/path', '/bad\x00path'):
            changed = copy.deepcopy(self.meta['wallpaper'])
            changed['Mounts'][0]['Destination'] = destination
            with self.subTest(destination=destination), self.assertRaises(RecoveryRequired):
                RecoveryObserver.contract(changed)

    def test_duplicate_or_aliased_destinations_fail_closed(self):
        for destination in ('/app/models', '/app/models/', '/app//models',
                            '/app/output/../models', '//app/models'):
            changed = copy.deepcopy(self.meta['wallpaper'])
            changed['Mounts'][0]['Destination'] = destination
            with self.subTest(destination=destination), self.assertRaises(RecoveryRequired):
                RecoveryObserver.contract(changed)

    def test_image_config_hostconfig_and_gpu_exposure_remain_bound(self):
        base = self.meta['wallpaper']
        expected = RecoveryObserver.contract(base)
        changes = [('Image', 'sha256:' + 'e' * 64),
                   ('Config', dict(base['Config'], Env=['CUDA_VISIBLE_DEVICES=1,0'])),
                   ('Config', dict(base['Config'], Cmd=['other-server'])),
                   ('Config', dict(base['Config'], Labels={'purpose': 'changed'})),
                   ('HostConfig', dict(base['HostConfig'], NetworkMode='changed')),
                   ('HostConfig', dict(base['HostConfig'], DeviceRequests=[{'DeviceIDs': ['GPU-0']}]))]
        for field, value in changes:
            changed = copy.deepcopy(base)
            changed[field] = value
            with self.subTest(field=field, value=value):
                self.assertNotEqual(RecoveryObserver.contract(changed), expected)

    def test_default_hostname_normalization_and_explicit_hostname_binding(self):
        base = self.meta['music']
        recreated = copy.deepcopy(base)
        recreated.update(Id='e' * 64)
        recreated['Config']['Hostname'] = 'e' * 12
        self.assertEqual(RecoveryObserver.contract(base), RecoveryObserver.contract(recreated))
        for explicit in ('approved-service', 'different-service'):
            changed = copy.deepcopy(base)
            changed['Config']['Hostname'] = explicit
            self.assertNotEqual(RecoveryObserver.contract(changed), RecoveryObserver.contract(base))
        changed['Config']['Hostname'] = 'approved-service'
        self.assertNotEqual(RecoveryObserver.contract(changed), RecoveryObserver.contract(
            dict(changed, Config=dict(changed['Config'], Hostname='different-service'))))

    def test_start_stop_changes_runtime_identity_but_not_contract(self):
        expected = RecoveryObserver.contract(self.meta['wallpaper'])
        identities = []
        for running, pid, started in ((False, 0, 'old'), (True, 123, 'new'), (False, 0, 'new')):
            self.meta['wallpaper']['State'].update(Running=running, Pid=pid, StartedAt=started)
            self.meta['wallpaper']['Mounts'].reverse()
            self.assertEqual(RecoveryObserver.contract(self.meta['wallpaper']), expected)
            identities.append(self.observed('wallpaper'))
        self.assertEqual([i['running'] for i in identities], [False, True, False])
        self.assertEqual([i['pid'] for i in identities], [0, 123, 0])
        self.assertNotEqual(identities[0]['started_at'], identities[1]['started_at'])

    def test_all_services_accept_permutations_but_reject_real_mount_drift(self):
        for service in self.meta:
            original = copy.deepcopy(self.meta[service])
            expected = self.observed(service)
            for mounts in itertools.permutations(MOUNTS):
                self.meta[service]['Mounts'] = copy.deepcopy(list(mounts))
                self.assertEqual(self.observed(service), expected)
            for field, value in (('Source', '/offline/foreign'), ('Destination', '/app/foreign'),
                                 ('RW', False), ('Mode', 'ro'), ('Propagation', 'rshared')):
                self.meta[service] = copy.deepcopy(original)
                self.meta[service]['Mounts'][0][field] = value
                with self.subTest(service=service, field=field), self.assertRaisesRegex(
                        RecoveryRequired, 'container_identity_or_supervision_changed'):
                    self.observed(service)
            self.meta[service] = original

    def test_only_nonwallpaper_recreation_remains_permitted(self):
        for service in self.meta:
            original = copy.deepcopy(self.meta[service])
            self.meta[service]['Id'] = 'e' * 64
            self.meta[service]['Config']['Hostname'] = 'e' * 12
            self.meta[service]['Mounts'].reverse()
            if service == 'wallpaper':
                # Exact-ID lookup itself fails when that container is absent.
                with self.assertRaises(RecoveryRequired):
                    self.observed(service)
            else:
                self.assertEqual(self.observed(service)['id'], 'e' * 64)
            self.meta[service] = original

    def test_service_name_image_supervision_and_gpu_contract_checks_remain_intact(self):
        for service in self.meta:
            original = copy.deepcopy(self.meta[service])
            for field, value in (('Name', '/foreign'), ('Image', 'sha256:' + 'e' * 64),
                                 ('HostConfig', dict(original['HostConfig'], DeviceRequests=[]))):
                self.meta[service] = copy.deepcopy(original)
                self.meta[service][field] = value
                with self.subTest(service=service, field=field), self.assertRaises(RecoveryRequired):
                    self.observed(service)
            for state in ('Paused', 'Dead', 'Restarting'):
                self.meta[service] = copy.deepcopy(original)
                self.meta[service]['State'][state] = True
                with self.subTest(service=service, state=state), self.assertRaises(RecoveryRequired):
                    self.observed(service)
            self.meta[service] = original


if __name__ == '__main__':
    unittest.main()
