"""Team ingestion CLI — scriptable Data Onboarding.

The team story: (1) register your connection, (2) ingest into your source,
(3) build your agent on it. This CLI is step 2 for pipelines:

    uv run python -m ace_agent_kit.ingest --config ingest.yaml \
        --ace-url http://localhost:3000 --cookie "<session>" --csrf "<token>"

ingest.yaml (everything is a parameter — ACE holds none of it):

    source_name: policies
    team_key: clinical_care
    connection: clinical_sharepoint
    location: {site_path: "/sites/policies", drive_name: "Documents", folder_path: ""}
    chunking:  {strategy: hierarchical, max_tokens: 512, overlap: 64}
    embedding: {deployment: "", vectors: both}
    access:
      agents: [policy_procedure]
      roles: [nurse, developer]
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml


class IngestionCli:
    def __init__(
        self, *, ace_url: str, cookie: str, csrf: str, poll_seconds: float = 3.0
    ) -> None:
        self._ace_url = ace_url.rstrip("/")
        self._headers = {"Cookie": cookie, "X-CSRF-Token": csrf}
        self._poll_seconds = poll_seconds

    async def run(self, config_path: str) -> int:
        payload = self._load(config_path)
        async with httpx.AsyncClient(
            base_url=self._ace_url, headers=self._headers, timeout=120
        ) as client:
            response = await client.post("/api/v1/knowledge/ingest/source", json=payload)
            if response.status_code >= 400:
                print(f"Ingestion rejected ({response.status_code}): {response.text}")
                return 1
            job_id = response.json()["data"]["job_id"]
            print(f"Ingestion started: job {job_id}")
            return await self._poll(client, job_id)

    async def _poll(self, client: httpx.AsyncClient, job_id: str) -> int:
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            response = await client.get(f"/api/v1/knowledge/ingest/jobs/{job_id}")
            response.raise_for_status()
            job = response.json()["data"]
            status = job["status"]
            if status in ("completed", "failed"):
                print(f"Job {job_id}: {status} — {job.get('detail', {})}")
                return 0 if status == "completed" else 1
            print(f"Job {job_id}: {status} ...")
            await asyncio.sleep(self._poll_seconds)
        print(f"Job {job_id}: timed out waiting for completion.")
        return 1

    @staticmethod
    def _load(config_path: str) -> dict[str, Any]:
        path = Path(config_path)
        if not path.is_file():
            raise FileNotFoundError(f"Ingestion config not found: {path}")
        with path.open("r", encoding="utf-8-sig") as file:
            data = yaml.safe_load(file) or {}
        if not isinstance(data, dict):
            raise ValueError("Ingestion config must be a yaml mapping.")
        return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a team data source into ACE.")
    parser.add_argument("--config", required=True, help="Path to ingest.yaml")
    parser.add_argument("--ace-url", required=True, help="ACE base URL")
    parser.add_argument("--cookie", required=True, help="ACE session cookie header value")
    parser.add_argument("--csrf", required=True, help="ACE CSRF token")
    args = parser.parse_args()
    cli = IngestionCli(ace_url=args.ace_url, cookie=args.cookie, csrf=args.csrf)
    sys.exit(asyncio.run(cli.run(args.config)))


if __name__ == "__main__":
    main()
