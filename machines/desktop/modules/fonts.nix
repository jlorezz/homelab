{ pkgs, ... }:
{
  fonts = {
    enableDefaultPackages = true;
    fontDir.enable = true;

    packages = with pkgs; [
      nerd-fonts.jetbrains-mono
      nerd-fonts.cascadia-mono
      noto-fonts
      noto-fonts-cjk-sans
      noto-fonts-emoji
      font-awesome
      liberation_ttf

      # MonoLisa (proprietary) — place TTF files in machines/desktop/modules/fonts/monolisa/
      # (pkgs.runCommand "monolisa-fonts" {} ''
      #   mkdir -p $out/share/fonts/truetype/monolisa
      #   cp ${./fonts/monolisa}/*.ttf $out/share/fonts/truetype/monolisa/
      # '')
    ];

    fontconfig.defaultFonts = {
      serif = [
        "Liberation Serif"
        "Noto Serif"
      ];
      sansSerif = [
        "Liberation Sans"
        "Noto Sans"
      ];
      monospace = [
        # "MonoLisa"  # uncomment after placing TTF files
        "JetBrainsMono Nerd Font"
      ];
      emoji = [ "Noto Color Emoji" ];
    };
  };
}
