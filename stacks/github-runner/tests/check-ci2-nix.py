#!/usr/bin/env python3
"""CI-only evaluated engine/cgroup contracts, including disabled-by-default."""
import json
import subprocess

expression = '''let
  f = builtins.getFlake (toString ./.);
  base = f.nixosConfigurations.nas;
  expanded = base.extendModules { modules = [{ qnoxslab.githubRunnerCI2.enable = true; }]; };
  c = expanded.config;
in {
  disabled = !(base.config.systemd.user.services ? docker-ci2);
  unit = c.systemd.user.services.docker-ci2;
  child = c.systemd.user.slices.dealpipe-ci2.sliceConfig;
  parent = c.systemd.slices.user-1102.sliceConfig;
  daemon = builtins.readFile (builtins.head (builtins.match ".*--config-file=(.*)" c.systemd.user.services.docker-ci2.serviceConfig.ExecStart));
}'''
result = json.loads(subprocess.check_output(['nix', 'eval', '--impure', '--json', '--expr', expression]))
assert result['disabled']
unit, child, parent = (result[key] for key in ('unit', 'child', 'parent'))
assert unit['unitConfig']['ConditionUser'] == 'dealpipe-ci'
assert unit['serviceConfig']['Slice'] == 'dealpipe-ci2.slice'
assert not unit['restartIfChanged'] and not unit['stopIfChanged']
assert child['MemoryMax'] == '16384M' and child['MemorySwapMax'] == 0
assert parent['MemoryMax'] == '32768M' and parent['MemorySwapMax'] == 0 and parent['CPUQuota'] == '800%'
env = unit['environment']
assert env['DOCKERD_ROOTLESS_ROOTLESSKIT_PORT_DRIVER'] == 'none'
assert env['DOCKERD_ROOTLESS_ROOTLESSKIT_DETACH_NETNS'] == 'false'
assert env['DOCKERD_ROOTLESS_ROOTLESSKIT_STATE_DIR'] == '/run/user/1102/dealpipe-ci2/rootlesskit'
daemon = json.loads(result['daemon'])
assert daemon['data-root'] == '/var/lib/dealpipe-ci/ci2/docker'
assert daemon['exec-root'] == '/run/user/1102/dealpipe-ci2/exec'
assert daemon['hosts'] == ['unix:///run/user/1102/dealpipe-ci2/docker.sock']
assert daemon['cgroup-parent'] == 'dealpipe-ci2.slice'
print('PASS evaluated CI2 isolation and unchanged parent')
