#!/usr/bin/env python3
"""Update silo-plugins catalog manifest.json from a plugin's GitHub release.

Usage: update-catalog.py --repo <owner/plugin-repo> --tag <vX.Y.Z>

Fetches the plugin's manifest.json (from release assets or repo root),
the checksums.txt, and the release binary list, then updates the
matching entry in the catalog manifest.json.

Does NOT push — the caller commits and pushes.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

GITHUB_API = "https://api.github.com"
PLATFORMS = [
    ("linux", "amd64"),
    ("linux", "arm64"),
    ("darwin", "arm64"),
]


def _api_request(url: str, token: str | None = None, expect_dict: bool = True):
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    with urlopen(req) as resp:
        result = json.loads(resp.read())
        return result


def api_get(url: str, token: str | None = None) -> dict:
    return _api_request(url, token, expect_dict=True)


def api_get_list(url: str, token: str | None = None) -> list:
    return _api_request(url, token, expect_dict=False)


def download_text(url: str, token: str | None = None) -> str:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    with urlopen(req) as resp:
        return resp.read().decode()


def parse_checksums(text: str) -> dict[str, str]:
    """Parse 'sha256  filename' lines into {filename: sha256}."""
    result = {}
    for line in text.strip().splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            result[parts[1].strip()] = parts[0].strip()
    return result


def normalize_manifest(raw: dict) -> dict:
    """Convert proto-style enum names to numeric values for catalog JSON.

    Silo's catalog loader uses Go encoding/json into proto structs, so
    enum values must be numeric integers, not string names.
    """
    control_map = {
        "ADMIN_FORM_CONTROL_TEXT": 1,
        "ADMIN_FORM_CONTROL_TEXTAREA": 2,
        "ADMIN_FORM_CONTROL_PASSWORD": 3,
        "ADMIN_FORM_CONTROL_SWITCH": 4,
        "ADMIN_FORM_CONTROL_SELECT": 5,
    }
    auth_method_map = {
        "WATCH_SYNC_AUTH_METHOD_API_KEY": 2,
        "WATCH_SYNC_AUTH_METHOD_DEVICE_CODE": 3,
        "WATCH_SYNC_AUTH_METHOD_AUTHORIZATION_CODE": 1,
    }
    media_type_map = {
        "WATCH_SYNC_MEDIA_TYPE_MOVIE": 1,
        "WATCH_SYNC_MEDIA_TYPE_EPISODE": 2,
    }

    def convert_form_fields(fields: list) -> list:
        for field in fields:
            ctrl = field.get("control")
            if isinstance(ctrl, str) and ctrl in control_map:
                field["control"] = control_map[ctrl]
        return fields

    for cap in raw.get("capabilities", []):
        # auth_provider: auth_modes are strings, keep as-is
        # watch_sync_provider: convert enum lists
        wsp = cap.get("watch_sync_provider")
        if wsp:
            if "auth_methods" in wsp:
                wsp["auth_methods"] = [
                    auth_method_map.get(m, m) if isinstance(m, str) else m
                    for m in wsp["auth_methods"]
                ]
            if "supported_media_types" in wsp:
                wsp["supported_media_types"] = [
                    media_type_map.get(m, m) if isinstance(m, str) else m
                    for m in wsp["supported_media_types"]
                ]
        # admin_form controls
        for cfg in raw.get("global_config_schema", []):
            form = cfg.get("admin_form")
            if form and "fields" in form:
                convert_form_fields(form["fields"])

    # Remove runtime-only placeholder checksum from manifest in catalog
    raw.pop("checksum", None)

    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description="Update catalog from plugin release")
    parser.add_argument("--repo", required=True, help="GitHub repo: owner/plugin-name")
    parser.add_argument("--tag", required=True, help="Release tag: vX.Y.Z")
    parser.add_argument("--token", default=None, help="GitHub token for private repos")
    parser.add_argument(
        "--catalog", default="manifest.json", help="Path to catalog manifest.json"
    )
    args = parser.parse_args()

    token = args.token or None

    # Fetch release info (try by-tag first, fall back to list for API lag)
    print(f"Fetching release {args.tag} from {args.repo}...")
    try:
        release = api_get(
            f"{GITHUB_API}/repos/{args.repo}/releases/tags/{args.tag}", token
        )
    except HTTPError as e:
        if e.code == 404:
            all_releases_raw: list = api_get_list(
                f"{GITHUB_API}/repos/{args.repo}/releases?per_page=20", token
            )
            release = next(
                (r for r in all_releases_raw if r.get("tag_name") == args.tag), None
            )
            if release is None:
                print(
                    f"ERROR: Release {args.tag} not found in {args.repo}",
                    file=sys.stderr,
                )
                return 1
        else:
            print(f"ERROR: Could not fetch release: {e}", file=sys.stderr)
            return 1

    tag = release["tag_name"]
    version = tag.lstrip("v")
    assets = {a["name"]: a for a in release.get("assets", [])}

    # Download checksums
    if "checksums.txt" not in assets:
        print(f"ERROR: No checksums.txt asset in release {tag}", file=sys.stderr)
        return 1
    checksums_url = assets["checksums.txt"]["browser_download_url"]
    checksums_text = download_text(checksums_url, token)
    checksums = parse_checksums(checksums_text)

    # Download manifest.json from release assets, or fall back to repo root
    raw_manifest = None
    if "manifest.json" in assets:
        manifest_url = assets["manifest.json"]["browser_download_url"]
        raw_manifest = json.loads(download_text(manifest_url, token))
    else:
        # Fetch from repo root at the tag
        try:
            raw_manifest = api_get(
                f"{GITHUB_API}/repos/{args.repo}/contents/manifest.json",
                token,
            )
            import base64

            raw_manifest = json.loads(
                base64.b64decode(raw_manifest["content"])
            )
        except (HTTPError, KeyError):
            print(
                f"WARNING: Could not fetch manifest.json from {args.repo}; skipping manifest update",
                file=sys.stderr,
            )

    # Build binary entries
    binaries = {}
    for goos, goarch in PLATFORMS:
        asset_name = f"plugin-{goos}-{goarch}"
        if asset_name in assets:
            binaries[f"{goos}/{goarch}"] = {
                "url": assets[asset_name]["browser_download_url"],
                "checksum": checksums.get(asset_name, ""),
            }

    if not binaries:
        print(f"ERROR: No plugin binaries found in release {tag}", file=sys.stderr)
        return 1

    # Load and update catalog
    catalog_path = Path(args.catalog)
    catalog = json.loads(catalog_path.read_text())

    plugin_id = raw_manifest["plugin_id"] if raw_manifest else None

    entry = {
        "repo_url": f"https://github.com/{args.repo}",
        "checksums_url": checksums_url,
        "binaries": binaries,
    }

    if raw_manifest:
        normalized = normalize_manifest(raw_manifest)
        normalized.pop("checksum", None)
        entry["manifest"] = normalized

    # Find existing entry or append
    updated = False
    for i, existing in enumerate(catalog["plugins"]):
        match_id = (
            existing.get("manifest", {}).get("plugin_id") == plugin_id
            if plugin_id
            else existing.get("repo_url") == entry["repo_url"]
        )
        if match_id:
            if raw_manifest:
                catalog["plugins"][i]["manifest"] = entry["manifest"]
            catalog["plugins"][i]["repo_url"] = entry["repo_url"]
            catalog["plugins"][i]["checksums_url"] = entry["checksums_url"]
            catalog["plugins"][i]["binaries"] = entry["binaries"]
            updated = True
            break

    if not updated:
        catalog["plugins"].append(entry)

    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n")
    print(f"Catalog updated: {plugin_id or args.repo} -> {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
