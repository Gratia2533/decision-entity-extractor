"""Clone-oriented commands with explicit service ownership."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError

from adapters.otter.artifact import ArtifactError, validate_artifact
from bootstrap import build_production_service
from contracts.errors import PipelineError
from contracts.models import ResolveRequest
from model_specs import MODELS
from runtime.config import load_schema


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("port must be an integer") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="entity-resolver")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("identities", help="Print pinned model identities")
    check = subcommands.add_parser("check", help="Fully verify local artifact bytes")
    check.add_argument("--artifact-root", type=Path, required=True)
    download = subcommands.add_parser("download", help="Provision and verify pinned artifacts")
    download.add_argument("--artifact-root", type=Path, default=Path(".local-artifacts"))
    for name in ("resolve", "serve"):
        command = subcommands.add_parser(name)
        command.add_argument("--artifact-root", type=Path, required=True)
        command.add_argument("--schema", type=Path, required=True)
        if name == "resolve":
            command.add_argument("--text", required=True)
        else:
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=_port, default=8000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "identities":
            output = {key: asdict(value) for key, value in MODELS.items()}
        elif args.command == "download":
            from adapters.otter.provisioning import download_artifacts

            output = download_artifacts(args.artifact_root)
        elif args.command == "check":
            output = {key: validate_artifact(args.artifact_root, key)[1] for key in MODELS}
        else:
            schema = load_schema(args.schema)
            request = ResolveRequest(text=args.text) if args.command == "resolve" else None
            if args.command == "serve":
                import uvicorn

                from transport.api import create_app
            service = build_production_service(artifact_root_path=args.artifact_root, schema=schema)
            try:
                if request is not None:
                    output = service.resolve(request).model_dump(mode="json")
                else:
                    uvicorn.run(create_app(service), host=args.host, port=args.port, workers=1)
                    return 0
            finally:
                service.close()
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except PipelineError as error:
        print(json.dumps(error.response().model_dump(mode="json")), file=sys.stderr)
    except (ValidationError, ValueError, OSError) as error:
        message = (
            str(error) if isinstance(error, ArtifactError) else "Invalid input or schema file."
        )
        print(json.dumps({"code": "INVALID_CONFIGURATION", "message": message}), file=sys.stderr)
    except ImportError:
        print(
            json.dumps({"code": "MISSING_DEPENDENCY", "message": "Install the required extras."}),
            file=sys.stderr,
        )
    except KeyboardInterrupt:
        return 130
    except Exception:
        print(json.dumps({"code": "COMMAND_FAILED", "message": "Command failed."}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
