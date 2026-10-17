import { DirectFileManipulator } from "npm:@vrtmrz/livesync-commonlib@0.1.34";
import { createTextBlob } from "npm:@vrtmrz/livesync-commonlib@0.1.34/compat/common/utils";
const cfg = JSON.parse(await Deno.readTextFile("/app/dat/config.json")).peers[0];
if (!cfg.database.startsWith("obsidian-smoke")) throw new Error("Use only an isolated smoke-test database");
const client = new DirectFileManipulator(cfg, { fetch: (r: Request, i: RequestInit) => globalThis.fetch(r, i) });
await client.ready.promise;
const meta = await client.get("NAS Created.md", true);
if (!meta) throw new Error("The NAS-created test note did not reach CouchDB");
const note = await client.getByMeta(meta);
if (!String(note.data).includes("NAS to encrypted CouchDB test")) throw new Error("Note content mismatch");
console.log("PASS: latest LiveSync client library decrypts the NAS-created note");
const contents = "Latest client to NAS filesystem test\n";
const now = Date.now();
await client.put("Client Created.md", createTextBlob(contents), { ctime: now, mtime: now, size: contents.length }, "plain");
console.log("PASS: latest LiveSync client library wrote an encrypted note");
await client.close();

Deno.exit(0);
