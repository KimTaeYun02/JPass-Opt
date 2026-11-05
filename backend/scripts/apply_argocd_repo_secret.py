"""Copy the current GitHub CLI token to Argo CD for this private Git repository.

Credentials stay in memory and are passed to kubectl on stdin. No token is
printed, written to a Secret file, or sent as a command-line argument.
"""

import argparse
import base64
import json
import subprocess
import sys

REPOSITORY = "https://github.com/KimTaeYun02/JPass-Opt.git"
SECRET_NAME = "jpass-git-repository"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="jpass-local")
    args = parser.parse_args()
    try:
        result = subprocess.run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        token = result.stdout.strip()
        if not token:
            raise ValueError("GitHub token is missing")
    except (
        OSError,
        ValueError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        print(
            "GitHub CLI 인증을 읽지 못했습니다. gh auth login을 확인해주세요.",
            file=sys.stderr,
        )
        return 2

    fields = {
        "type": "git",
        "url": REPOSITORY,
        "username": "x-access-token",
        "password": token,
        "project": "jpass-local",
    }
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": SECRET_NAME,
            "namespace": "argocd",
            "labels": {"argocd.argoproj.io/secret-type": "repository"},
        },
        "type": "Opaque",
        "data": {
            key: base64.b64encode(value.encode("utf-8")).decode("ascii")
            for key, value in fields.items()
        },
    }
    try:
        subprocess.run(
            [
                "kubectl",
                "--context",
                args.context,
                "--namespace",
                "argocd",
                "apply",
                "--server-side",
                "--field-manager=jpass-argocd-repository",
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
            "Git 인증 Secret 적용에 실패했습니다. argocd namespace와 접근 권한을 확인해주세요.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Argo CD Git 인증 적용 완료: context={args.context}, namespace=argocd, name={SECRET_NAME}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
