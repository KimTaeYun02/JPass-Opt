"""Copy the current Docker login for ghcr.io to a Kubernetes image-pull Secret.

Reads only GHCR credentials from Docker's config or credential helper. Values
stay in memory and are passed to kubectl on stdin, without writing a Secret file.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

REGISTRY = "ghcr.io"
SECRET_NAME = "ghcr-pull-secret"


def ghcr_auth() -> str:
    directory = Path(os.environ.get("DOCKER_CONFIG") or Path.home() / ".docker")
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    helper = config.get("credHelpers", {}).get(REGISTRY) or config.get("credsStore")
    if helper:
        result = subprocess.run(
            [f"docker-credential-{helper}", "get"],
            input=REGISTRY + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        credential = json.loads(result.stdout)
        username, password = credential["Username"], credential["Secret"]
        if not username or not password or username == "<token>":
            raise ValueError("A GHCR username and personal access token are required")
        return base64.b64encode(f"{username}:{password}".encode()).decode("ascii")

    auths = config.get("auths", {})
    credential = next(
        (
            auths[key]
            for key in (REGISTRY, f"https://{REGISTRY}", f"https://{REGISTRY}/v1/")
            if key in auths
        ),
        {},
    )
    auth = credential.get("auth", "")
    username, password = (
        base64.b64decode(auth, validate=True).decode("utf-8").split(":", 1)
    )
    if not username or not password:
        raise ValueError("GHCR login is missing")
    return auth


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="jpass-local")
    parser.add_argument("--namespace", default="jpass")
    args = parser.parse_args()

    try:
        auth = ghcr_auth()
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        # Docker/credential helper output may contain tokens; never echo it.
        print(
            "GHCR 로그인 정보를 읽지 못했습니다. docker login ghcr.io를 확인해주세요.",
            file=sys.stderr,
        )
        return 2

    docker_config = json.dumps({"auths": {REGISTRY: {"auth": auth}}})
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": SECRET_NAME, "namespace": args.namespace},
        "type": "kubernetes.io/dockerconfigjson",
        "data": {
            ".dockerconfigjson": base64.b64encode(docker_config.encode()).decode(
                "ascii"
            )
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
                "--field-manager=jpass-ghcr-secrets",
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
        print(
            "GHCR Secret 적용에 실패했습니다. context·namespace·접근 권한을 확인해주세요.",
            file=sys.stderr,
        )
        return 1

    print(
        f"GHCR Secret 적용 완료: context={args.context}, namespace={args.namespace}, name={SECRET_NAME}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
