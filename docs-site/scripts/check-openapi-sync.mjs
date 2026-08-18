import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const siteRoot = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const repoRoot = path.resolve(siteRoot, '..');
const artifacts = [
  {
    sourcePath: path.join(repoRoot, 'docs', 'openapi.json'),
    publishedPath: path.join(siteRoot, 'public', 'openapi.json'),
  },
  {
    sourcePath: path.join(repoRoot, 'docs', 'remnawave-minishop.webp'),
    publishedPath: path.join(siteRoot, 'public', 'remnawave-minishop.webp'),
  },
];

async function readArtifact(artifactPath) {
  try {
    return await readFile(artifactPath);
  } catch (error) {
    console.error(`Failed to read ${path.relative(repoRoot, artifactPath)}.`);
    throw error;
  }
}

let artifactsMatch = true;
for (const { sourcePath, publishedPath } of artifacts) {
  const [source, published] = await Promise.all([
    readArtifact(sourcePath),
    readArtifact(publishedPath),
  ]);
  if (!source.equals(published)) {
    console.error(
      `${path.relative(repoRoot, publishedPath)} is out of sync with ${path.relative(repoRoot, sourcePath)}.`,
    );
    artifactsMatch = false;
  }
}

if (!artifactsMatch) {
  console.error('Run `npm --prefix docs-site run sync:docs` and commit the updated artifacts.');
  process.exit(1);
}

console.log('Published documentation artifacts are in sync with their sources.');
