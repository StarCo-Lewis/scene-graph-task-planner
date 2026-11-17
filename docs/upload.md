# First publication to the new repository

Repository: [StarCo-Lewis/scene-graph-task-planner](https://github.com/StarCo-Lewis/scene-graph-task-planner).

The owner supplied this new repository address. No network access was used to inspect or upload it. The original GO2 repository is left unchanged. There is no repository-renaming or old-tree deletion step.

## Upload from PowerShell

Run from the extracted release root. The commands below clone the repository into a fresh sibling directory, refuse a repository that already has remote branches, and copy only files pinned in the source lock. Local runs, environments and unlisted files are not copied. Use your own configured Git identity and GitHub authentication.

~~~powershell
$ReleaseDir = (Get-Location).Path
if (-not (Test-Path -LiteralPath (Join-Path $ReleaseDir "release_source_lock.json"))) { throw "Run from the extracted release root." }
python -m sgtp verify-lock
if ($LASTEXITCODE -ne 0) { throw "Extract a clean release before uploading." }

$PublishDir = Join-Path (Split-Path -Parent $ReleaseDir) "scene-graph-task-planner-publish"
if (Test-Path -LiteralPath $PublishDir) { throw "Choose a fresh publication directory." }
git clone https://github.com/StarCo-Lewis/scene-graph-task-planner.git "$PublishDir"
if ($LASTEXITCODE -ne 0) { throw "Clone failed; check your access and the repository URL." }
$RemoteBranches = @(git -C "$PublishDir" for-each-ref '--format=%(refname)' refs/remotes/origin/)
if ($LASTEXITCODE -ne 0) { throw "Could not inspect the cloned references." }
if ($RemoteBranches.Count -ne 0) { throw "The repository already has branches. Stop here and adapt the instructions to preserve its history." }
git -C "$PublishDir" switch -c main
if ($LASTEXITCODE -ne 0) { throw "Could not prepare the initial main branch." }

$SourceLock = Get-Content -LiteralPath (Join-Path $ReleaseDir "release_source_lock.json") -Raw | ConvertFrom-Json
$PinnedNames = @($SourceLock.files.PSObject.Properties.Name) + "release_source_lock.json"
foreach ($Name in $PinnedNames) {
  $From = Join-Path $ReleaseDir $Name
  $To = Join-Path $PublishDir $Name
  New-Item -ItemType Directory -Path (Split-Path -Parent $To) -Force | Out-Null
  Copy-Item -LiteralPath $From -Destination $To -Force
}
Push-Location -LiteralPath $PublishDir
try {
  python -m sgtp verify-lock
  if ($LASTEXITCODE -ne 0) { throw "The copied publication tree failed its source lock." }
} finally {
  Pop-Location
}
git -C "$PublishDir" add --all
if ($LASTEXITCODE -ne 0) { throw "Could not stage the release." }
git -C "$PublishDir" status --short
git -C "$PublishDir" diff --cached --stat
~~~

Review the staged files, then publish the initial commit:

~~~powershell
git -C "$PublishDir" commit -m "Publish scene graph task planner"
if ($LASTEXITCODE -ne 0) { throw "Commit failed; check your configured Git identity." }
git -C "$PublishDir" push -u origin main
if ($LASTEXITCODE -ne 0) { throw "Upload failed. Keep the local commit and inspect the reported error; do not force-push." }
~~~

The clone and push are network operations performed by the repository owner. No model API or robot command is part of this process. If GitHub already contains an initial README or license commit, these instructions stop before copying files so that its history can be retained.

## Scope and licensing

The source tree contains the three-stage planner, route-topology preprocessing, symbolic catalogues, English documentation and historical diagnostic summaries. Navigation controllers, field integration, private scene assets and raw private logs are excluded. No new software/data license is granted by preparing this package; choose an appropriate license after confirming ownership and applicable notices.
