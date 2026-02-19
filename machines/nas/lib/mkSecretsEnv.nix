# mkSecretsEnv - Generates a systemd service that builds a secrets .env file
#
# Usage:
#   mkSecretsEnv {
#     name = "postgres";
#     vars = {
#       POSTGRES_USER = "postgres-user";      # Maps env var name -> clan secret generator name
#       POSTGRES_PASSWORD = "postgres-password";
#     };
#     extraLines = [ "POSTGRES_DB=$(cat ${userPath})" ];  # Optional extra lines
#   }
#
# Returns a NixOS config fragment with tmpfiles + systemd service

{ config }:

{
  name,
  vars,
  extraLines ? [ ],
}:

let
  secretsDir = "/run/${name}-secrets";
  envFile = "${secretsDir}/secrets.env";
  podmanService = "podman-${name}.service";

  # Build env lines from the vars attrset
  envLines = builtins.attrValues (
    builtins.mapAttrs (
      envVar: secretName:
      ''echo "${envVar}=$(cat ${
        config.clan.core.vars.generators.${secretName}.files.secret.path
      })" >> ${envFile}''
    ) vars
  );

  extraEnvLines = map (line: ''echo "${line}" >> ${envFile}'') extraLines;
in
{
  systemd.tmpfiles.rules = [
    "d ${secretsDir} 0700 root root -"
  ];

  systemd.services."${name}-secrets" = {
    description = "Generate ${name} secrets environment file";
    wantedBy = [ podmanService ];
    before = [ podmanService ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = builtins.concatStringsSep "\n" (
      [ "rm -f ${envFile}" ] ++ envLines ++ extraEnvLines ++ [ "chmod 600 ${envFile}" ]
    );
  };
}
