{
  config,
  lib,
  ...
}:
{
  options.qnoxslab.nvidia = {
    enable = lib.mkEnableOption "NVIDIA proprietary driver with Wayland support";
    openKernel = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Use nvidia-open kernel module (Turing+ / RTX required)";
    };
  };

  config = lib.mkIf config.qnoxslab.nvidia.enable {
    hardware.nvidia = {
      modesetting.enable = true;
      powerManagement.enable = true;
      powerManagement.finegrained = false;
      open = config.qnoxslab.nvidia.openKernel;
      nvidiaSettings = true;
      package = config.boot.kernelPackages.nvidiaPackages.stable;
    };

    services.xserver.videoDrivers = [ "nvidia" ];

    hardware.graphics = {
      enable = true;
      enable32Bit = true;
    };

    environment.sessionVariables = {
      LIBVA_DRIVER_NAME = "nvidia";
      __GLX_VENDOR_LIBRARY_NAME = "nvidia";
      NVD_BACKEND = "direct";
    };

    boot.kernelParams = [
      "nvidia-drm.modeset=1"
      "nvidia-drm.fbdev=1"
    ];
  };
}
