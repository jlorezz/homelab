{ pkgs }:

let
  opencode = import ./opencode.nix { inherit pkgs; };
  t3 = import ./t3-nightly.nix { inherit pkgs opencode; };
  runtimes = import ./dealpipe-runtimes.nix { inherit pkgs; };
  workload = pkgs.writeShellScriptBin "agent-workload" ''
    exec ${pkgs.python3}/bin/python3 ${../stacks/agents/workload.py} "$@"
  '';
  tools = with pkgs; [
    bashInteractive
    coreutils
    findutils
    gnugrep
    gnused
    gawk
    git
    gh
    openssh
    curl
    jq
    ripgrep
    cacert
    python3
    runtimes.node
    runtimes.bun
    workload
    t3
    opencode
  ];
in
pkgs.dockerTools.buildLayeredImage {
  name = "localhost/homelab-agents";
  # Omit tag: dockerTools uses the derivation hash, so each build is immutable.
  contents = tools ++ [
    pkgs.dockerTools.binSh
    pkgs.dockerTools.usrBinEnv
  ];
  extraCommands = ''
    mkdir -p etc tmp home/agent workspace
    chmod 1777 tmp
    echo 'agent:x:1100:1100:Development agent:/home/agent:/bin/bash' > etc/passwd
    echo 'agent:x:1100:' > etc/group
    echo 'hosts: files dns' > etc/nsswitch.conf
  '';
  config = {
    User = "1100:1100";
    WorkingDir = "/workspace";
    Env = [
      "HOME=/home/agent"
      "PATH=/bin"
      "SSL_CERT_FILE=/etc/ssl/certs/ca-bundle.crt"
      "GIT_SSL_CAINFO=/etc/ssl/certs/ca-bundle.crt"
    ];
    Cmd = [
      "${t3}/bin/t3"
      "serve"
      "--host"
      "0.0.0.0"
      "--port"
      "3773"
    ];
    ExposedPorts = {
      "3773/tcp" = { };
      "4096/tcp" = { };
    };
  };
}
