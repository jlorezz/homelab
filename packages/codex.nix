{ pkgs }:

# Keep the complete official platform bundle: tool execution requires the
# sibling code-mode host and packaged resources, not just the CLI executable.
# Credentials remain in the persistent agent home.
pkgs.stdenv.mkDerivation {
  pname = "codex-cli";
  version = "0.164.0";
  src = pkgs.fetchurl {
    url = "https://github.com/openai/codex/releases/download/rust-v0.164.0/codex-npm-linux-x64-0.164.0.tgz";
    hash = "sha256-YmWf79mo76IaSWQC/ePBHAJbvu5zWlgpaPj/0Uy195w=";
  };
  dontStrip = true;
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/libexec/codex" "$out/bin"
    cp -a vendor/x86_64-unknown-linux-musl/. "$out/libexec/codex/"
    ln -s ../libexec/codex/bin/codex "$out/bin/codex"
    ln -s ../libexec/codex/bin/codex-code-mode-host "$out/bin/codex-code-mode-host"
    runHook postInstall
  '';
  doInstallCheck = true;
  installCheckPhase = ''
    test -x "$out/libexec/codex/bin/codex-code-mode-host"
    test -f "$out/libexec/codex/codex-package.json"
    "$out/bin/codex" --version | grep -F '0.164.0'
  '';
  meta = {
    description = "OpenAI Codex CLI for the persistent NAS agent runtime";
    homepage = "https://github.com/openai/codex";
    license = pkgs.lib.licenses.asl20;
    platforms = [ "x86_64-linux" ];
    mainProgram = "codex";
  };
}
