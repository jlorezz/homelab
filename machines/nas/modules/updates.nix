{ config, lib, pkgs, ... }:

let
  # Select the newest kernel whose packaged ZFS module supports it.
  compatibleKernels = lib.filterAttrs (
    name: packages:
    builtins.match "linux_[0-9]+_[0-9]+" name != null
    && (builtins.tryEval packages).success
    && !packages.${config.boot.zfs.package.kernelModuleAttribute}.meta.broken
  ) pkgs.linuxKernel.packages;
  latestKernel = lib.last (
    lib.sort (a: b: lib.versionOlder a.kernel.version b.kernel.version)
      (builtins.attrValues compatibleKernels)
  );

  # These upstream releases are newer than the current unstable channel.
  immichSource = pkgs.fetchFromGitHub {
    owner = "immich-app";
    repo = "immich";
    tag = "v3.3.3";
    hash = "sha256-GVQgmXpTJ6Aj6LgR7NgsYAQgzY4RZTDZp4dafD4OG5A=";
  };
  immichMachineLearning = pkgs.immich-machine-learning.overrideAttrs {
    version = "3.3.3";
    src = "${immichSource}/machine-learning";
  };
in
{
  boot.kernelPackages = latestKernel;
  hardware.enableRedistributableFirmware = true;
  hardware.graphics.enable = true;
  environment.systemPackages = [ pkgs.pciutils ];

  services.postgresql.package = pkgs.postgresql_18;

  services.immich.package = (pkgs.immich.override {
    immich-machine-learning = immichMachineLearning;
  }).overrideAttrs {
    version = "3.3.3";
    src = immichSource;
    pnpmDeps = pkgs.fetchPnpmDeps {
      pname = "immich";
      version = "3.3.3";
      src = immichSource;
      pnpm = pkgs.pnpm_11;
      fetcherVersion = 4;
      hash = "sha256-/HgmMv+YaWh26FIYiBlIdeObh0R5aacJx59FStxrCP0=";
    };
  };

  services.paperless.package = pkgs.paperless-ngx.overrideAttrs {
    version = "3.3.1";
    src = pkgs.fetchFromGitHub {
      owner = "paperless-ngx";
      repo = "paperless-ngx";
      tag = "v3.3.1";
      hash = "sha256-Njw5VpjwBkrhmoQGBF/dmQ7smz/RW3RyN+T8op3sLx4=";
    };
  };

}
