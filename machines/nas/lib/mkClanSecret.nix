# mkClanSecret - Eliminates repeated clan.core.vars.generators boilerplate
#
# Usage:
#   mkClanSecret { name = "postgres-password"; description = "PostgreSQL superuser password"; }
#   mkClanSecret { name = "postgres-user"; description = "PostgreSQL superuser username"; promptType = "line"; }
#
# Returns an attrset suitable for `clan.core.vars.generators // (mkClanSecret { ... })`

{
  name,
  description,
  promptType ? "hidden",
  neededFor ? "services",
}:

{
  ${name} = {
    prompts.value = {
      inherit description;
      type = promptType;
    };
    files.secret = {
      secret = true;
      inherit neededFor;
    };
    script = ''
      cat $prompts/value > $out/secret
    '';
  };
}
