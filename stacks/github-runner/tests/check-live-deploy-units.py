#!/usr/bin/env python3
"""CI-only: the NAS config must reproduce the live deploy units (C1 reconciliation).

The production hash is the sha256 of /etc/systemd/system/dealpipe-deploy-production.service
read on the NAS (2026-10-24 baseline, re-read 2026-10-25). A switch whose generation
differs from it is refused by the operator gate, so CI fails first.
Staging must equal the pre-parallelism (4bfead6) evaluation that built the live unit.
"""
import hashlib
import json
import os
import subprocess
import sys

LIVE_PRODUCTION_SHA256 = "fc4e319c5d1a929c5d1f63dfd7313c12b6689dd9f73799f31f48759d4fe2c933"
BASE = "4bfead617fc26e6d634b1696061729716127822b"
# The image in the generated unit file; a persistent drop-in overrides it at runtime.
IMAGE = "sha256:840686ed03a267899e312868e6287a5436e6b34d5b85afc1fac00124eca56b0b"


def evaluate(flake, expr_body):
    expression = f'let f = builtins.getFlake "{flake}"; in {expr_body}'
    return subprocess.check_output(["nix", "eval", "--impure", "--raw", "--expr", expression], text=True)


def units(flake, configure):
    body = ('let s = ' + configure + '; in builtins.toJSON {'
            ' production = s.config.systemd.units."dealpipe-deploy-production.service".text;'
            ' staging = s.config.systemd.units."dealpipe-deploy-staging.service".text; }')
    return json.loads(evaluate(flake, body))


here = units(f"path:{os.getcwd()}", "f.nixosConfigurations.nas")
base_dir = sys.argv[1]
before = units(f"path:{base_dir}", '''f.nixosConfigurations.nas.extendModules { modules = [{
  qnoxslab.githubDeploymentRunners = { enable = true; environments = [ "staging" "production" ];
    image = "%s"; }; }]; }''' % IMAGE)

def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


production = digest(here["production"])
print(f"production unit sha256 {production} (live {LIVE_PRODUCTION_SHA256})")
print(f"4bfead6 production unit sha256 {digest(before['production'])}")
if production != LIVE_PRODUCTION_SHA256:
    # Unit text carries paths and arguments only (the token is a LoadCredential path).
    print("---- evaluated production unit ----")
    print(here["production"])
    print("---- end ----")
assert production == LIVE_PRODUCTION_SHA256, "production unit would differ from the live NAS unit"
assert here["production"] == before["production"], "production unit differs from the 4bfead6 evaluation"
assert here["staging"] == before["staging"], "staging unit differs from the 4bfead6 evaluation (live shape)"
print("staging unit sha256", hashlib.sha256(here["staging"].encode()).hexdigest())
print("live deploy units reproduced")
