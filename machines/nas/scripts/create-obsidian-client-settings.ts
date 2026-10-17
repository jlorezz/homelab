/** Run inside the bridge container. Writes private files; never logs credentials. */
import { DirectFileManipulator } from "npm:@vrtmrz/livesync-commonlib@0.1.34";
import { upsertRemoteConfigurationInPlace } from "npm:@vrtmrz/livesync-commonlib@0.1.34/remote-configurations";
import { encodeTimeBoundSetupURI } from "npm:@vrtmrz/livesync-commonlib@0.1.34/setup-uri";
const config = JSON.parse(await Deno.readTextFile("/app/dat/config.json")).peers[0];
const client = new DirectFileManipulator(config, { fetch: (r: Request, i: RequestInit) => globalThis.fetch(r, i) });
await client.ready.promise;
const note = await client.get("Brain/Start Here.md");
if (!note || !String(note.data).includes("# Brain")) throw new Error("The knowledge base is not available in the sync database");
const settings = {
  ...client.settings,
  couchDB_URI: "https://nas.tailb80645.ts.net:5984",
  isConfigured: true,
  liveSync: true,
  syncOnStart: true,
  syncOnFileOpen: true,
  syncAfterMerge: true,
  periodicReplication: true,
  batchSave: true,
};
upsertRemoteConfigurationInPlace(settings, "couchdb", { activate: true });
const secret = Array.from(crypto.getRandomValues(new Uint8Array(24)), v => v.toString(16).padStart(2,"0")).join("");
const encoded = await encodeTimeBoundSetupURI(settings, secret, { mode: "persistent", skipDefaultValue: true });
for (const [filename, content] of [
  ["client-settings.json", JSON.stringify(settings)],
  ["iphone-setup-uri.txt", encoded.uri],
  ["iphone-setup-passphrase.txt", secret],
]) {
  const path = `/app/dat/${filename}`;
  await Deno.writeTextFile(path, content);
  await Deno.chmod(path, 0o600);
}
console.log("PASS: knowledge base decrypted with the current client library; private desktop settings and iPhone setup files prepared");
await client.close();
Deno.exit(0);
