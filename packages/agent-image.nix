{ pkgs }:

let
  opencode = import ./opencode.nix { inherit pkgs; };
  t3 = import ./t3-nightly.nix { inherit pkgs opencode; };
  codex = import ./codex.nix { inherit pkgs; };
  runtimes = import ./dealpipe-runtimes.nix { inherit pkgs; };
  # T3 downloads its pinned Chrome binary into the persistent home. Supply its
  # ELF loader, libraries and fonts declaratively; the worker cannot apt install.
  browserLibraries = with pkgs; [
    glibc stdenv.cc.cc.lib nss nspr glib atk at-spi2-atk at-spi2-core dbus
    libx11 libxcb libxcomposite libxdamage
    libxext libxfixes libxrandr
    libxkbcommon libgbm alsa-lib expat fontconfig systemdLibs
  ];
  browserFonts = pkgs.makeFontsConf { fontDirectories = [ pkgs.liberation_ttf ]; };
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
    fontconfig
    runtimes.node
    runtimes.bun
    workload
    t3
    codex
    claude-code
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
    mkdir -p etc tmp home/agent workspace lib64
    ln -s ${pkgs.glibc}/lib/ld-linux-x86-64.so.2 lib64/ld-linux-x86-64.so.2
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
      "LD_LIBRARY_PATH=${pkgs.lib.makeLibraryPath browserLibraries}"
      "FONTCONFIG_FILE=${browserFonts}"
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
