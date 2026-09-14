import { spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const checkOnly = process.argv.includes('--check');
const mobileRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = resolve(mobileRoot, '..');
const backendRoot = resolve(repositoryRoot, 'backend');
const contract = resolve(repositoryRoot, 'contracts', 'openapi.json');
const target = resolve(mobileRoot, 'lib', 'generated', 'api.ts');
const temporaryRoot = await mkdtemp(resolve(tmpdir(), 'reader-openapi-'));
const schema = resolve(temporaryRoot, 'openapi.json');
const generatedRoot = resolve(temporaryRoot, 'generated');
const generated = resolve(generatedRoot, 'types.gen.ts');

function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, encoding: 'utf8', stdio: 'pipe' });
  if (result.status !== 0) {
    process.stderr.write(result.stdout ?? '');
    process.stderr.write(result.stderr ?? '');
    process.exitCode = result.status ?? 1;
    throw new Error(`Command failed: ${command}`);
  }
}

try {
  run(
    resolve(backendRoot, '.venv', 'bin', 'python'),
    ['scripts/export_openapi.py', '--output', schema],
    backendRoot,
  );
  run(
    resolve(mobileRoot, 'node_modules', '.bin', 'openapi-ts'),
    ['--input', schema, '--output', generatedRoot],
    mobileRoot,
  );
  // Publish the same schema used for type generation, only after generation succeeds.
  const artifacts = [
    { path: contract, contents: await readFile(schema, 'utf8') },
    { path: target, contents: await readFile(generated, 'utf8') },
  ];
  if (checkOnly) {
    const stale = [];
    for (const artifact of artifacts) {
      const current = await readFile(artifact.path, 'utf8').catch((error) => {
        if (error.code !== 'ENOENT') throw error;
        return null;
      });
      if (current !== artifact.contents) {
        stale.push(relative(repositoryRoot, artifact.path));
      }
    }
    if (stale.length > 0) {
      throw new Error(
        `API contract artifacts are stale or missing: ${stale.join(', ')}. Run pnpm run generate:api-types.`,
      );
    }
    console.log('OpenAPI snapshot and API types match the FastAPI contract.');
  } else {
    for (const artifact of artifacts) {
      await mkdir(dirname(artifact.path), { recursive: true });
      await writeFile(artifact.path, artifact.contents, 'utf8');
      console.log(`Generated ${artifact.path}`);
    }
  }
} finally {
  await rm(temporaryRoot, { force: true, recursive: true });
}
