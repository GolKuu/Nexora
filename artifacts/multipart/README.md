# Multipart local artifacts

These files preserve the Windows training environment and the local KASE LoRA
checkpoint without exceeding GitHub's 100 MB per-file limit. Each numbered part
is at most 94,000,000 bytes. Manifests contain SHA-256 checksums for every part
and for the reconstructed ZIP archive.

Restore either artifact from the repository root:

```powershell
.\scripts\restore_large_artifact.ps1 -Artifact venv-train-windows
.\scripts\restore_large_artifact.ps1 -Artifact kase-ai-1.7b-v0.2-checkpoint-25
```

For verification or extraction somewhere other than the repository root, pass
`-Destination`, for example:

```powershell
.\scripts\restore_large_artifact.ps1 -Artifact kase-ai-1.7b-v0.2-checkpoint-25 -Destination .\tmp\model-check
```

The virtual environment is Windows/Python-version specific. For a clean or
cross-platform setup, installing dependencies from the project's locked
requirements remains preferable.

To regenerate all parts from the local source directories:

```powershell
.\scripts\package_large_artifacts.ps1
```
