{ pkgs, opencode ? pkgs.opencode }:

# Pin an official nightly archive so image builds remain reproducible.
pkgs.stdenv.mkDerivation (finalAttrs: {
  pname = "t3code-nightly";
  version = "0.1.3-nightly.20261023.3553";

  src = pkgs.fetchurl {
    url = "https://github.com/pingdotgg/t3code/releases/download/v${finalAttrs.version}/t3-${finalAttrs.version}-linux-x64.tar.gz";
    hash = "sha256-67y+QBpI/fx20/yD/s0IaoYm4QIQnZudLlczNUgJiRU=";
  };

  nativeBuildInputs = [ pkgs.autoPatchelfHook pkgs.makeWrapper ];
  buildInputs = [ pkgs.stdenv.cc.cc.lib pkgs.zlib pkgs.musl ];
  dontStrip = true; # Preserve the executable's embedded Node application.

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/libexec/t3code" "$out/bin"
    cp -a . "$out/libexec/t3code/"
    makeWrapper "$out/libexec/t3code/t3" "$out/bin/t3" \
      --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.git pkgs.gh opencode ]}
    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    "$out/bin/t3" --version | grep -F '${finalAttrs.version}'
    runHook postInstallCheck
  '';

  meta = {
    description = "T3 Code nightly server from the official CLI release";
    homepage = "https://github.com/pingdotgg/t3code";
    license = pkgs.lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    mainProgram = "t3";
  };
})
