{ pkgs }:
{
  # Match Dealpipe's packageManager and .node-version. Update with the app, never
  # by installing a floating runtime into a persistent agent home.
  bun = pkgs.stdenv.mkDerivation {
    pname = "bun-dealpipe";
    version = "1.3.11";
    src = pkgs.fetchurl {
      url = "https://github.com/oven-sh/bun/releases/download/bun-v1.3.11/bun-linux-x64-baseline.zip";
      hash = "sha256-q+NG9jQUVHzfazW3pkmkkMcouT0AYiYVaSORioTA5Zs=";
    };
    nativeBuildInputs = [ pkgs.unzip pkgs.autoPatchelfHook ];
    buildInputs = [ pkgs.stdenv.cc.cc.lib ];
    dontStrip = true;
    dontBuild = true;
    installPhase = ''
      install -Dm755 bun "$out/bin/bun"
      ln -s bun "$out/bin/bunx"
    '';
    doInstallCheck = true;
    installCheckPhase = ''
      test "$($out/bin/bun --version)" = 1.3.11
    '';
  };
  node = pkgs.stdenv.mkDerivation {
    pname = "nodejs-dealpipe";
    version = "22.23.3";
    src = pkgs.fetchurl {
      url = "https://nodejs.org/dist/v22.23.3/node-v22.23.3-linux-x64.tar.xz";
      hash = "sha256-30UK+JJhEV75+eODDD7rLMkhO2PHILGvYjy13L4uAt4=";
    };
    nativeBuildInputs = [ pkgs.autoPatchelfHook ];
    buildInputs = [ pkgs.stdenv.cc.cc.lib ];
    dontBuild = true;
    installPhase = ''
      mkdir -p "$out"
      cp -a bin include lib share "$out/"
    '';
    doInstallCheck = true;
    installCheckPhase = ''
      test "$($out/bin/node --version)" = v22.23.3
    '';
  };
}
