import argparse
import os
import socket
import sys
import urllib.error
import urllib.request
from typing import Dict, List, Tuple

from dotenv import load_dotenv


load_dotenv()


GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"{GREEN}OK{RESET}  {msg}")


def _warn(msg: str) -> None:
    print(f"{YELLOW}WARN{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"{RED}FAIL{RESET} {msg}")


def _present(name: str) -> bool:
    value = os.getenv(name)
    return bool(value and value.strip())


def _check_port(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.7)
        return sock.connect_ex((host, port)) == 0


def _http_get(url: str, timeout: float = 2.5) -> Tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as err:
        return False, f"HTTP error {err.code}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _check_env(analyst_provider: str, writer_provider: str) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    providers = {analyst_provider, writer_provider}
    if "gemini" in providers and not _present("GOOGLE_API_KEY"):
        errors.append("GOOGLE_API_KEY is missing (required for Gemini provider).")
    if "openai" in providers and not _present("OPENAI_API_KEY"):
        errors.append("OPENAI_API_KEY is missing (required for OpenAI provider).")
    if "groq" in providers and not _present("GROQ_API_KEY"):
        errors.append("GROQ_API_KEY is missing (required for Groq provider).")

    if "ollama" in providers:
        if not _present("OLLAMA_BASE_URL"):
            warnings.append("OLLAMA_BASE_URL not set; defaulting to http://localhost:11434")
        if not _present("OLLAMA_MODEL"):
            warnings.append("OLLAMA_MODEL not set; defaulting to llama3.1")
        if not _present("OLLAMA_API_KEY"):
            warnings.append("OLLAMA_API_KEY is missing (only required for cloud-hosted Ollama).")

    if not _present("OPENAI_MODEL"):
        warnings.append("OPENAI_MODEL not set; defaulting to gpt-4o")
    if not _present("GEMINI_MODEL"):
        warnings.append("GEMINI_MODEL not set; defaulting to gemini-2.0-flash")
    if not _present("GROQ_MODEL"):
        warnings.append("GROQ_MODEL not set; defaulting to llama-3.1-8b-instant")

    return errors, warnings


def _check_live_services(analyst_provider: str, writer_provider: str) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    providers = {analyst_provider, writer_provider}

    if "ollama" in providers:
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        healthy, detail = _http_get(f"{base_url}/api/tags")
        if not healthy:
            errors.append(f"Cannot reach Ollama at {base_url}/api/tags ({detail}).")
        else:
            _ok(f"Ollama reachable at {base_url} ({detail})")

    if _check_port("127.0.0.1", 3010):
        warnings.append("Port 3010 already appears in use (UI launch may fail).")
    else:
        _ok("Port 3010 is available for UI server")

    return errors, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight checks for article pipeline")
    parser.add_argument(
        "--analyst-provider",
        choices=["gemini", "openai", "ollama", "groq"],
        default="ollama",
        help="Planned provider for analyst node",
    )
    parser.add_argument(
        "--writer-provider",
        choices=["gemini", "openai", "ollama", "groq"],
        default="ollama",
        help="Planned provider for writer node",
    )
    parser.add_argument(
        "--check-live",
        action="store_true",
        help="Also check local service reachability (Ollama + port availability)",
    )
    args = parser.parse_args()

    print("\nArticle Pipeline Preflight\n")
    print(f"- analyst provider: {args.analyst_provider}")
    print(f"- writer provider:  {args.writer_provider}")

    env_errors, env_warnings = _check_env(args.analyst_provider, args.writer_provider)

    for warning in env_warnings:
        _warn(warning)
    if not env_errors:
        _ok("Environment variables look good for selected providers")
    else:
        for err in env_errors:
            _fail(err)

    live_errors: List[str] = []
    live_warnings: List[str] = []
    if args.check_live:
        print("\nRunning live checks...\n")
        live_errors, live_warnings = _check_live_services(
            args.analyst_provider, args.writer_provider
        )
        for warning in live_warnings:
            _warn(warning)
        for err in live_errors:
            _fail(err)

    all_errors = env_errors + live_errors
    if all_errors:
        print("\nPreflight result: FAILED\n")
        sys.exit(1)

    print("\nPreflight result: PASSED\n")


if __name__ == "__main__":
    main()
