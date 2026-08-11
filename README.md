# Mildman1848 Silo Plugins

Plugin catalog for Silo-compatible plugins maintained by Mildman1848.

## Repository source URL

Add this URL to Silo as a plugin repository source:

```text
https://raw.githubusercontent.com/mildman1848/silo-plugins/main/manifest.json
```

## Included plugins

| Plugin | Capability | Repository |
|---|---|---|
| Watcharr Sync | `watch_sync_provider.v1` | https://github.com/mildman1848/silo-plugin-sync-watcharr |

## Structure

This repository mirrors the upstream Silo catalog format used by:

- `https://raw.githubusercontent.com/Silo-Server/silo-plugins/main/manifest.json`
- `https://raw.githubusercontent.com/Silo-Community/silo-plugins/main/manifest.json`

Each entry contains:

- embedded plugin manifest
- upstream plugin repository URL
- release checksum URL
- downloadable binaries per platform

## Security notes

Only add catalog sources you trust. Plugin binaries execute as part of your Silo deployment, so treat catalogs like package repositories, not like a harmless shopping list. Humans have historically confused these two. It ended poorly.
