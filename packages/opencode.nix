{ pkgs }:

# Use the official CLI release until the flake's nixpkgs catches up.
pkgs.stdenv.mkDerivation (finalAttrs: {
  pname = "opencode";
  version = "1.19.0";

  src = pkgs.fetchurl {
    url = "https://github.com/anomalyco/opencode/releases/download/v${finalAttrs.version}/opencode-linux-x64.tar.gz";
    hash = "sha256-e7nmGtb+rPLonBCmosGEDj3kIgu1C6H8cRDzGAYHauw=";
  };
  sourceRoot = ".";
  nativeBuildInputs = [ pkgs.autoPatchelfHook pkgs.makeWrapper ];
  buildInputs = [ pkgs.stdenv.cc.cc.lib pkgs.zlib ];
  dontStrip = true; # Preserve the embedded Bun application.

  installPhase = ''
    runHook preInstall
    install -Dm755 opencode "$out/libexec/opencode"
    makeWrapper "$out/libexec/opencode" "$out/bin/opencode" \
      --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.ripgrep ]} \
      --set OPENCODE_DISABLE_AUTOUPDATE true \
      --run '
        data_home="''${XDG_DATA_HOME:-$HOME/.local/share}"
        if [ -z "''${OPENCODE_DB:-}" ] \
          && [ -z "''${NIXPKGS_OPENCODE_DISABLE_LEGACY_DB_WORKAROUND:-}" ] \
          && [ -e "$data_home/opencode/opencode-stable.db" ] \
          && [ ! -e "$data_home/opencode/opencode.db" ]; then
          export OPENCODE_DB="opencode-stable.db"
        fi
      '
    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    HOME=$(mktemp -d) TMPDIR=$(mktemp -d) \
      "$out/bin/opencode" --version | grep -Fx '${finalAttrs.version}'
    runHook postInstallCheck
  '';

  meta = {
    description = "OpenCode CLI from the official release";
    homepage = "https://github.com/anomalyco/opencode";
    license = pkgs.lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    mainProgram = "opencode";
  };
})
