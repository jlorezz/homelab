{
  inputs.clan-core.url = "https://git.clan.lol/clan/clan-core/archive/main.tar.gz";
  inputs.nixpkgs.follows = "clan-core/nixpkgs";

  outputs =
    {
      self,
      clan-core,
      nixpkgs,
      ...
    }@inputs:
    let
      # Usage see: https://docs.clan.lol
      clan = clan-core.lib.clan {
        inherit self;
        imports = [ ./clan.nix ];
        specialArgs = { inherit inputs; };

        # Customize nixpkgs
        # pkgsForSystem =
        #   system:
        #   import nixpkgs {
        #     inherit system;
        #     config = {
        #       allowUnfree = true;
        #     };
        #     overlays = [];
        #   };
      };
    in
    {
      inherit (clan.config) nixosConfigurations nixosModules clanInternals;
      packages.x86_64-linux.agent-image = import ./packages/agent-image.nix {
        pkgs = nixpkgs.legacyPackages.x86_64-linux;
      };
      packages.x86_64-linux.agent-runtimes =
        let
          pkgs = nixpkgs.legacyPackages.x86_64-linux;
          runtimes = import ./packages/dealpipe-runtimes.nix { inherit pkgs; };
        in pkgs.symlinkJoin {
          name = "dealpipe-agent-runtimes";
          paths = [ runtimes.bun runtimes.node ];
        };
      packages.x86_64-linux.configure-agents =
        let pkgs = nixpkgs.legacyPackages.x86_64-linux; in
        pkgs.writeShellApplication {
          name = "configure-agents";
          runtimeInputs = [ (pkgs.python3.withPackages (p: [ p.pyyaml ])) ];
          text = ''
            exec python3 ${./stacks/agents/configure.py} --source ${./stacks/agents} "$@"
          '';
        };
      packages.x86_64-linux.agent-doctor =
        let pkgs = nixpkgs.legacyPackages.x86_64-linux; in
        pkgs.writeShellApplication {
          name = "agent-doctor";
          runtimeInputs = [ (pkgs.python3.withPackages (p: [ p.pyyaml ])) pkgs.gh ];
          # Use the complete source tree so local skill links are checked too.
          text = ''
            exec python3 ${./.}/stacks/agents/doctor.py "$@"
          '';
        };
      packages.x86_64-linux.review-agents =
        let pkgs = nixpkgs.legacyPackages.x86_64-linux; in
        pkgs.writeShellApplication {
          name = "review-agents";
          runtimeInputs = [ (pkgs.python3.withPackages (p: [ p.pyyaml ])) pkgs.git pkgs.gitleaks ];
          text = ''
            exec python3 ${./stacks/agents}/review.py "$@"
          '';
        };
      packages.x86_64-linux.deploy-agents =
        let
          pkgs = nixpkgs.legacyPackages.x86_64-linux;
        in
        pkgs.writeShellApplication {
          name = "deploy-agents";
          runtimeInputs = [ (pkgs.python3.withPackages (p: [ p.pyyaml ])) ];
          text = ''
            exec python3 ${./stacks/agents/deploy.py} --stack-directory ${./stacks/agents} "$@"
          '';
        };
      packages.x86_64-linux.prepare-agent-workspaces =
        let
          pkgs = nixpkgs.legacyPackages.x86_64-linux;
        in
        pkgs.writeShellApplication {
          name = "prepare-agent-workspaces";
          runtimeInputs = with pkgs; [
            git
            openssl
            util-linux
            coreutils
            (python3.withPackages (p: [ p.pyyaml ]))
          ];
          text = ''
            export HERMES_CONFIG_TEMPLATE=${./stacks/agents/hermes-config.yaml}
            export OPENCODE_CONFIG_TEMPLATE=${./stacks/agents/opencode.json}
            export AGENT_POLICY_SOURCE=${./stacks/agents}
            exec bash ${./stacks/agents/prepare-workspaces.sh} "$@"
          '';
        };
      clan = clan.config;
      # Add the Clan cli tool to the dev shell.
      # Use "nix develop" to enter the dev shell.
      devShells =
        nixpkgs.lib.genAttrs
          [
            "x86_64-linux"
            "aarch64-linux"
            "aarch64-darwin"
            "x86_64-darwin"
          ]
          (system: {
            default = clan-core.inputs.nixpkgs.legacyPackages.${system}.mkShell {
              packages = [ clan-core.packages.${system}.clan-cli ];
            };
          });
    };
}
