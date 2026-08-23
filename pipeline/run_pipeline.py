#!/usr/bin/env python3
"""Run the complete Assembly PDF-to-BigQuery pipeline in the required order."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def command(script: str, *args: object) -> list[str]:
    """현재 Python 실행기로 하위 단계 명령을 조립한다."""
    return [sys.executable, str(ROOT / script), *(str(value) for value in args)]


def run(cmd: list[str], dry_run: bool) -> None:
    """명령을 표시하고 실행한다. dry-run이면 출력만 한다."""
    print("\n+ " + " ".join(cmd), flush=True)
    if not dry_run:
        subprocess.run(cmd, cwd=ROOT, check=True)


def parse_args() -> argparse.Namespace:
    """수집 범위, GCP 대상 및 안전 실행 옵션을 읽는다."""
    parser = argparse.ArgumentParser(
        description="Collect Assembly PDFs and rebuild all BigQuery search documents"
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--years",
        nargs="+",
        type=int,
        help="calendar years to collect, for example --years 2024 2025",
    )
    scope.add_argument(
        "--skip-collect",
        action="store_true",
        help="do not call the Assembly API; rebuild from data already in GCS/BigQuery",
    )
    parser.add_argument("--project", default="proj-aj04-211200020328")
    parser.add_argument("--dataset", default="assembly")
    parser.add_argument("--bucket", default="proj-aj04-211200020328-assembly-us")
    parser.add_argument("--assembly-no", type=int, default=22)
    parser.add_argument(
        "--first-year-start",
        type=date.fromisoformat,
        help="optional first collection date, for example 2024-05-30",
    )
    parser.add_argument(
        "--last-date",
        type=date.fromisoformat,
        help="optional last collection date in the final year",
    )
    parser.add_argument("--reprocess-existing", action="store_true")
    parser.add_argument(
        "--dry-run", action="store_true", help="print commands without executing them"
    )
    return parser.parse_args()


def preflight(args: argparse.Namespace) -> None:
    """필수 파일, API 키와 pdftotext 설치 여부를 실행 전에 검사한다."""
    missing = [
        script
        for script in (
            "step01_collect_assembly.py",
            "step02_rebuild_pdf_tables.py",
            "step03_normalize_legislators.py",
            "step04_build_search_documents.py",
            "step05_validate_search_documents.py",
            "step06_build_vote_search_documents.py",
            "step07_validate_vote_search_documents.py",
        )
        if not (ROOT / script).is_file()
    ]
    if missing:
        raise SystemExit(f"missing pipeline files: {', '.join(missing)}")
    if not args.dry_run and not os.environ.get("ASSEMBLY_API_KEY"):
        raise SystemExit("set ASSEMBLY_API_KEY in the environment")
    if not args.dry_run and shutil.which("pdftotext") is None:
        raise SystemExit("pdftotext is required; install Poppler first")
    if args.years:
        years = sorted(set(args.years))
        if args.first_year_start and args.first_year_start.year != years[0]:
            raise SystemExit("--first-year-start must belong to the first --years value")
        if args.last_date and args.last_date.year != years[-1]:
            raise SystemExit("--last-date must belong to the final --years value")


def main() -> int:
    """수집부터 두 검색 테이블 검증까지 정해진 순서로 실행한다."""
    args = parse_args()
    preflight(args)
    common = [
        "--project", args.project,
        "--dataset", args.dataset,
        "--bucket", args.bucket,
        "--assembly-no", args.assembly_no,
    ]

    if args.years:
        years = sorted(set(args.years))
        for index, year in enumerate(years):
            collect_args: list[object] = ["--year", year, *common]
            if index == 0 and args.first_year_start:
                collect_args += ["--start-date", args.first_year_start]
            if index == len(years) - 1 and args.last_date:
                collect_args += ["--end-date", args.last_date]
            if args.reprocess_existing:
                collect_args.append("--reprocess-existing")
            run(command("step01_collect_assembly.py", *collect_args), args.dry_run)

    run(command("step02_rebuild_pdf_tables.py", *common, "--apply"), args.dry_run)
    run(command("step03_normalize_legislators.py", *common, "--apply"), args.dry_run)
    run(
        command(
            "step04_build_search_documents.py",
            "--project", args.project,
            "--dataset", args.dataset,
        ),
        args.dry_run,
    )
    run(
        command(
            "step05_validate_search_documents.py",
            "--project", args.project,
            "--dataset", args.dataset,
        ),
        args.dry_run,
    )
    run(
        command(
            "step06_build_vote_search_documents.py",
            "--project", args.project,
            "--dataset", args.dataset,
            "--apply",
        ),
        args.dry_run,
    )
    run(
        command(
            "step07_validate_vote_search_documents.py",
            "--project", args.project,
            "--dataset", args.dataset,
        ),
        args.dry_run,
    )
    if args.dry_run:
        print("\nDRY RUN COMPLETE: no commands were executed.", flush=True)
    else:
        print(
            "\nPIPELINE PASS\n"
            "BigQuery is complete. FULL Import search_documents and "
            "vote_search_documents into their Vertex AI Search Data Stores.",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(
            f"\nPIPELINE FAILED: command exited with {error.returncode}\n"
            "No later step was executed.",
            file=sys.stderr,
        )
        raise SystemExit(error.returncode)
