// Optional PostgreSQL-engine smoke check. No network, AutoCAD, or production database.
// Install @electric-sql/pglite in a separate disposable tooling directory, then:
// node scripts/check-translation-migration.mjs <that-directory>
import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';

const tooling = process.argv[2];
if (!tooling) throw new Error('Pass the separate PGlite tooling directory.');
const require = createRequire(resolve(tooling, 'package.json'));
const { PGlite } = require('@electric-sql/pglite');
const db = await PGlite.create();
const directory = new URL('../services/gateway/migrations/', import.meta.url);
const id = (prefix, number = 1) => `${prefix}_00000000-0000-4000-8000-${String(number).padStart(12, '0')}`;
const tenant = id('tnt');
try {
  const migrations = (await readdir(directory)).filter(name => name.endsWith('.sql')).sort();
  for (const name of migrations) await db.exec(await readFile(new URL(name, directory), 'utf8'));
  await db.query(`INSERT INTO cadplot_gateway.workstations
    (workstation_id, tenant_id, owner_subject_id, display_name) VALUES ($1,$2,$3,'Fixture')`,
    [id('ws'), tenant, id('usr')]);
  const request = { command: 'translation', drawing_id: id('drw'), request: { operation: 'apply' } };
  await db.query(`INSERT INTO cadplot_gateway.operations
    (operation_id,tenant_id,owner_subject_id,client_id,workstation_id,idempotency_key,
     request_fingerprint,task,created_at,expires_at)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,now(),now()+interval '10 minutes')`,
    [id('op'),tenant,id('usr'),id('cli'),id('ws'),id('idem'),'a'.repeat(64),JSON.stringify(request)]);
  await db.query(`UPDATE cadplot_gateway.operations SET state='LEASED',
    lease_expires_at=now()+interval '1 minute' WHERE operation_id=$1`, [id('op')]);
  await db.query(`UPDATE cadplot_gateway.operations SET state='RUNNING',
    lease_expires_at=NULL, started_at=now() WHERE operation_id=$1`, [id('op')]);
  await db.query(`UPDATE cadplot_gateway.operations SET state='ATTENTION_REQUIRED',
    completed_at=now(), error_code='translation_attention_required' WHERE operation_id=$1`, [id('op')]);
  assert.equal((await db.query('SELECT state FROM cadplot_gateway.operations')).rows[0].state, 'ATTENTION_REQUIRED');
  await assert.rejects(() => db.query(`UPDATE cadplot_gateway.operations SET state='CREATED',
    completed_at=NULL,error_code=NULL,started_at=NULL WHERE operation_id=$1`, [id('op')]), /transition_forbidden/);
  await assert.rejects(() => db.query(`UPDATE cadplot_gateway.operations SET task='{}' WHERE operation_id=$1`, [id('op')]), /immutable|mutation|binding/);
  await db.exec('SET ROLE cadplot_gateway_runtime');
  await db.query("SELECT set_config('cadplot.tenant_id', $1, false)", [id('tnt', 2)]);
  assert.equal((await db.query('SELECT count(*)::int AS count FROM cadplot_gateway.operations')).rows[0].count, 0);
  await db.query("SELECT set_config('cadplot.tenant_id', $1, false)", [tenant]);
  assert.equal((await db.query('SELECT count(*)::int AS count FROM cadplot_gateway.operations')).rows[0].count, 1);
  await assert.rejects(() => db.exec('ALTER TABLE cadplot_gateway.operations ADD COLUMN forbidden text'), /owner|permission/);
  console.log(JSON.stringify({passed:true,migrations:migrations.length,engine:'PGlite PostgreSQL',
    attention_transition:true,terminal_replay_rejected:true,immutable_task:true,tenant_isolation:true,runtime_ddl_denied:true}));
} finally {
  await db.close();
}
