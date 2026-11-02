"""Copy only API keys from Compose's resolved environment to a Kubernetes Secret.

Uses the Python standard library, Docker Compose and kubectl. Secret values are
captured in memory and passed to kubectl through stdin; no Secret file is saved.
"""

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEYS = ("GOOGLE_PLACE_API_KEY", "GEMINI_API_KEY")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="jpass-local")
    parser.add_argument("--namespace", default="jpass")
    args = parser.parse_args()

    try:
        compose = subprocess.run(
            ["docker", "compose", "-f", "compose.yaml", "config", "--format", "json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        environment = json.loads(compose.stdout)["services"]["api"]["environment"]
        if not isinstance(environment, dict):
            raise ValueError("Compose environment must be an object")
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        ValueError,
        KeyError,
        TypeError,
    ):
        # Compose output can contain secrets, so never include it in errors.
        print(
            "Compose 설정을 읽지 못했습니다. Docker Compose와 루트 .env를 확인해주세요.",
            file=sys.stderr,
        )
        return 2

    values = {key: str(environment.get(key) or "").strip() for key in KEYS}
    missing = [key for key in KEYS if not values[key]]
    if missing:
        print("API 키가 비어 있습니다: " + ", ".join(missing), file=sys.stderr)
        return 2

    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "jpass-api-secrets", "namespace": args.namespace},
        "type": "Opaque",
        "data": {
            key: base64.b64encode(values[key].encode("utf-8")).decode("ascii")
            for key in KEYS
        },
    }
    try:
        subprocess.run(
            [
                "kubectl",
                "--context",
                args.context,
                "--namespace",
                args.namespace,
                "apply",
                "--server-side",
                "--field-manager=jpass-local-secrets",
                "-f",
                "-",
            ],
            input=json.dumps(secret),
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # An API error may echo request data; avoid printing its raw body.
        print(
            "Secret 적용에 실패했습니다. kubectl context·namespace·접근 권한을 확인해주세요.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Secret 적용 완료: context={args.context}, namespace={args.namespace}, name=jpass-api-secrets"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
