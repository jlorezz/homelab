{ config, lib, pkgs, ... }:

let
  secrets = config.clan.core.vars.generators.obsidian-sync.files;
  bridgeImage = "localhost/livesync-bridge:5645cf6a74e8-commonlib34-v2";
  bridgeSource = pkgs.fetchFromGitHub {
    owner = "vrtmrz";
    repo = "livesync-bridge";
    rev = "5645cf6a74e863ccc29bc2e9dedef1c4941e096b";
    hash = "sha256-CjAU2ThV33M86mHbf+9YfIsAgPymmj9VGd+bNV7fXpc=";
  };
  bridgeBuildSource = pkgs.runCommand "livesync-bridge-build-source" {
    nativeBuildInputs = [ pkgs.patch ];
  } ''
    cp -r ${bridgeSource} $out
    chmod -R u+w $out
    cp ${../config/obsidian-bridge/deno.jsonc} $out/deno.jsonc
    cp ${../config/obsidian-bridge/deno.lock} $out/deno.lock
    patch -d $out -p1 < ${../config/obsidian-bridge/compat.patch}
    printf '\nRUN deno cache --frozen main.ts\n' >> $out/Dockerfile
  '';
in
{
  clan.core.vars.generators.obsidian-sync = {
    files = {
      admin-password.secret = true;
      sync-password.secret = true;
      vault-passphrase.secret = true;
    };
    runtimeInputs = [ pkgs.openssl ];
    script = ''
      openssl rand -hex 32 > $out/admin-password
      openssl rand -hex 32 > $out/sync-password
      openssl rand -hex 32 > $out/vault-passphrase
    '';
  };

  environment.etc."obsidian/couchdb.ini".text = ''
    [couchdb]
    single_node = true
    max_document_size = 50000000
    [chttpd]
    require_valid_user = true
    enable_cors = true
    max_http_request_size = 4294967296
    [chttpd_auth]
    require_valid_user = true
    [httpd]
    WWW-Authenticate = Basic realm="Obsidian NAS"
    [cors]
    origins = app://obsidian.md,capacitor://localhost,http://localhost
    credentials = true
    methods = GET, PUT, POST, HEAD, DELETE, OPTIONS
    headers = accept, authorization, content-type, origin, referer
    [cluster]
    n = 1
    [log]
    level = warn
  '';

  virtualisation.oci-containers.containers.obsidian-couchdb = {
    image = "docker.io/library/couchdb:3.5.3";
    user = "5984:5984";
    ports = [ "127.0.0.1:5984:5984" ];
    volumes = [
      "/var/lib/obsidian-couchdb:/opt/couchdb/data"
      "/etc/obsidian/couchdb.ini:/opt/couchdb/etc/local.d/10-obsidian.ini:ro"
    ];
    environmentFiles = [ "/run/obsidian-sync/couchdb.env" ];
    extraOptions = [ "--memory=1g" "--memory-swap=1g" "--cpus=2" ];
  };
  virtualisation.oci-containers.containers.obsidian-bridge = {
    autoStart = true;
    image = bridgeImage;
    user = "1993:100";
    cmd = [ "sh" "-c" "umask 0002; test -r /app/dat/config.json && exec deno task run" ];
    dependsOn = [ "obsidian-couchdb" ];
    volumes = [
      "/var/lib/obsidian-bridge/data:/app/data"
      "/storage/obsidian:/app/data/vault"
      "/var/lib/obsidian-bridge/dat:/app/dat"
      "/run/obsidian-bridge-secrets/config.json:/app/dat/config.json:ro"
      "/var/lib/obsidian-bridge/location-data:/deno-dir/location_data"
    ];
    extraOptions = [
      "--network=host" "--memory=1g" "--memory-swap=1g" "--cpus=2"
      "--log-driver=journald"
    ];
  };
  systemd.services.obsidian-bridge-watchdog = {
    description = "Recover a stalled Obsidian bridge";
    serviceConfig.Type = "oneshot";
    path = [ pkgs.systemd pkgs.docker pkgs.coreutils ];
    script = ''
      # Leave deliberately stopped or backup-paused services alone.
      systemctl is-active --quiet docker-obsidian-bridge.service || exit 0
      if [ "$(docker inspect obsidian-bridge --format '{{.State.Paused}}')" = true ]; then
        exit 0
      fi
      if ! timeout 15s docker exec obsidian-bridge deno eval \
        'const h=JSON.parse(Deno.readTextFileSync("/tmp/lsb-health.json")); Deno.exit(Date.now()-h.ts<120000 && !h.restartWorthy ? 0 : 1)'; then
        echo "Obsidian bridge heartbeat stalled; restarting the service."
        systemctl restart docker-obsidian-bridge.service
      fi
    '';
  };
  systemd.timers.obsidian-bridge-watchdog = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "3m";
      OnUnitActiveSec = "2m";
    };
  };
  systemd.services.docker-obsidian-bridge = {
    requires = [ "obsidian-bridge-secrets.service" "obsidian-provision.service" "zfs-mount.service" ];
    after = [ "obsidian-bridge-secrets.service" "obsidian-provision.service" "zfs-mount.service" ];
    unitConfig.RequiresMountsFor = [ "/storage/obsidian" ];
    preStart = lib.mkBefore ''
      ${pkgs.util-linux}/bin/mountpoint -q /storage/obsidian
      # Private/ exists only as encrypted CouchDB documents; a plaintext copy is a leak.
      if [ -e /storage/obsidian/Private ] || [ -e /storage/obsidian/private ]; then
        echo "Refusing to start: plaintext Private/ found in /storage/obsidian" >&2
        exit 1
      fi
      if ! ${pkgs.docker}/bin/docker image inspect ${bridgeImage} >/dev/null 2>&1; then
        ${pkgs.docker}/bin/docker build --tag ${bridgeImage} ${bridgeBuildSource}
      fi
    '';
  };
  systemd.services.obsidian-bridge-secrets = {
    wantedBy = [ "docker-obsidian-bridge.service" ];
    before = [ "docker-obsidian-bridge.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    script = ''
      install -d -m 750 -o obsidian-bridge -g users /run/obsidian-bridge-secrets
      ${pkgs.python3}/bin/python3 - <<'PYTHON'
      import json
      from pathlib import Path
      password = Path("${secrets.sync-password.path}").read_text().strip()
      passphrase = Path("${secrets.vault-passphrase.path}").read_text().strip()
      config = {"excludePrefixes": ["Private/"], "peers": [
        {"type": "couchdb", "name": "nas-couchdb", "group": "notes",
         "url": "http://127.0.0.1:5984", "database": "obsidian",
         "username": "notes-sync", "password": password, "baseDir": "",
         "passphrase": passphrase, "obfuscatePassphrase": passphrase,
         "useRemoteTweaks": True},
        {"type": "storage", "name": "nas-files", "group": "notes",
         "baseDir": "/app/data/vault", "scanOfflineChanges": True,
         "useChokidar": True}
      ]}
      Path("/run/obsidian-bridge-secrets/config.json").write_text(json.dumps(config))
      PYTHON
      chown obsidian-bridge:users /run/obsidian-bridge-secrets/config.json
    '';
  };

  systemd.services.obsidian-sync-secrets = {
    wantedBy = [ "docker-obsidian-couchdb.service" ];
    before = [ "docker-obsidian-couchdb.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    script = ''
      install -d -m 700 /run/obsidian-sync
      printf 'COUCHDB_USER=nas-admin\nCOUCHDB_PASSWORD=%s\n' \
        "$(cat ${secrets.admin-password.path})" > /run/obsidian-sync/couchdb.env
    '';
  };
  systemd.services.docker-obsidian-couchdb = {
    requires = [ "obsidian-sync-secrets.service" ];
    after = [ "obsidian-sync-secrets.service" ];
    restartTriggers = [ config.environment.etc."obsidian/couchdb.ini".source ];
  };
  systemd.services.obsidian-provision = {
    wantedBy = [ "multi-user.target" ];
    requires = [ "docker-obsidian-couchdb.service" ];
    after = [ "docker-obsidian-couchdb.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      TimeoutStartSec = "90s";
    };
    script = ''
      ${pkgs.python3}/bin/python3 ${../scripts/provision-obsidian.py} \
        ${secrets.admin-password.path} ${secrets.sync-password.path}
    '';
  };
  systemd.tmpfiles.rules = [
    "d /var/lib/obsidian-couchdb 0700 5984 5984 -"
    "d /var/lib/obsidian-bridge 0750 obsidian-bridge users -"
    # The vault's shared group contains jlorezz and obsidian-bridge. Setgid
    # keeps new top-level notes in that group; the ZFS dataset has ACLs off.
    "d /storage/obsidian 2770 jlorezz users -"
    "d /var/lib/obsidian-bridge/data 0750 obsidian-bridge users -"
    "d /var/lib/obsidian-bridge/dat 0700 obsidian-bridge users -"
    "d /var/lib/obsidian-bridge/location-data 0700 obsidian-bridge users -"
  ];
  users.users.obsidian-bridge = {
    isSystemUser = true;
    uid = 1993;
    group = "users";
  };
}
