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
  unit = { inherit (c.systemd.user.services.docker-ci2)
    unitConfig serviceConfig environment restartIfChanged stopIfChanged postStart; };
  child = c.systemd.user.slices.dealpipe-ci2.sliceConfig;
  parent = c.systemd.slices.user-1102.sliceConfig;
  browser = { inherit (c.systemd.user.services.docker-browser)
    environment restartIfChanged stopIfChanged; };
  daemon = c.qnoxslab.githubRunnerCI2.daemonSettings;
  mainDaemon = c.virtualisation.docker.rootless.daemon.settings;
  wrapper = c.qnoxslab.githubRunnerCI2.namespaceSetupScript;
  launcher = c.qnoxslab.githubRunnerCI2.launcherScript;
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
assert '"$DOCKERD_ROOTLESS_ROOTLESSKIT_STATE_DIR/resolv.conf"' in unit['postStart']
assert env['XDG_RUNTIME_DIR'] == '%t/dealpipe-ci2'
assert env['DOCKERD_ROOTLESS_ROOTLESSKIT_FLAGS'] == '--copy-up=/var/lib/dealpipe-ci'
assert env['DOCKERD'].startswith('/nix/store/') and env['DOCKERD'].endswith('-ci2-dockerd-namespace')
assert '-ci2-rootless-launcher --config-file=/nix/store/' in unit['serviceConfig']['ExecStart']
assert result['browser']['environment']['XDG_RUNTIME_DIR'] == '%t/dealpipe-browser'
assert not result['browser']['restartIfChanged'] and not result['browser']['stopIfChanged']
daemon = result['daemon']
assert daemon['data-root'] == '/var/lib/dealpipe-ci/docker'
assert daemon['exec-root'] == '/run/user/1102/dealpipe-ci2/exec'
assert daemon['hosts'] == ['unix:///run/user/1102/dealpipe-ci2/docker.sock']
assert daemon['cgroup-parent'] == 'dealpipe-ci2.slice'
assert daemon['default-address-pools'] == [{'base': '10.203.0.0/16', 'size': 24}]
assert result['mainDaemon']['default-address-pools'] == [{'base': '10.201.0.0/16', 'size': 24}]
wrapper = result['wrapper']
assert '_DOCKERD_ROOTLESS_CHILD:-' in wrapper
assert '/proc/self/ns/$ns' in wrapper and '/proc/1/' not in wrapper
assert 'CI2_PARENT_USER_NS' in wrapper and 'CI2_PARENT_MNT_NS' in wrapper
assert '/bin/id -ru)' in result['launcher'] and '= 1102' in result['launcher']
assert '/bin/readlink /proc/self/ns/user' in result['launcher']
assert '/bin/readlink /proc/self/ns/mnt' in result['launcher']
assert '/bin/id -ru' in wrapper
assert '/bin/unlink "$base/$name"' in wrapper and '/bin/rm' not in wrapper
assert 'mount --bind "$base/ci2/$name" "$base/$name"' in wrapper
assert 'stat -Lc' in wrapper
assert 'exec /nix/store/' in wrapper and '/bin/dockerd "$@"' in wrapper
print('PASS evaluated CI2 source contracts and unchanged parent; runtime isolation UNVERIFIED')
