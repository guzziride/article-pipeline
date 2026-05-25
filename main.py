import argparse
import json
import os
from typing import Any, Dict

from dotenv import load_dotenv
from langgraph.types import Command

from graph import build_graph
from settings import get_default_topic


load_dotenv()


def _print_shortlist(result: Dict[str, Any]) -> None:
    candidates = result.get("curated_candidates", [])
    if not candidates:
        print("No shortlisted articles were returned by the analyst.")
        return

    print("\nShortlisted articles:\n")
    for item in candidates:
        print(f"[{item.get('id')}] {item.get('title')}")
        print(f"    URL: {item.get('url')}")
        print(f"    Source: {item.get('source')}")
        print(f"    Relevance: {item.get('relevance_score')}")
        print()


def _assert_env() -> None:
    # Tavily is now optional because scout can run RSS-only routes.
    # Runtime provider-specific key failures are surfaced by each node.
    return


def _print_state_snapshot(app: Any, config: Dict[str, Any]) -> None:
    state = app.get_state(config)
    values = state.values if isinstance(state.values, dict) else {}
    payload = {
        "next": list(state.next) if state.next else [],
        "workflow_status": values.get("workflow_status"),
        "selected_article_id": values.get("selected_article_id"),
        "candidate_count": len(values.get("curated_candidates", [])),
        "has_final_draft": bool(values.get("final_draft")),
    }
    print("\nCheckpoint state:\n")
    print(json.dumps(payload, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the article pipeline with HITL.")
    parser.add_argument(
        "--thread-id",
        default="linkedin-demo-thread",
        help="Checkpoint thread id (must be reused when resuming)",
    )
    parser.add_argument(
        "--topic",
        default=get_default_topic(),
        help="Search topic for Tavily",
    )
    parser.add_argument(
        "--include-domains",
        default=None,
        help="Optional comma-separated domains to constrain scout sources",
    )
    parser.add_argument(
        "--analyst-provider",
        choices=["gemini", "openai", "ollama", "groq"],
        default="ollama",
        help="LLM provider for analyst node",
    )
    parser.add_argument(
        "--writer-provider",
        choices=["openai", "gemini", "ollama", "groq"],
        default="ollama",
        help="LLM provider for writer node",
    )
    parser.add_argument(
        "--analyst-model",
        default=None,
        help="Optional model override for analyst provider (e.g., llama3.1)",
    )
    parser.add_argument(
        "--writer-model",
        default=None,
        help="Optional model override for writer provider (e.g., llama3.1)",
    )
    parser.add_argument(
        "--selected-article-id",
        default=None,
        help="Provide this to resume after interrupt using Command(resume=...)",
    )
    parser.add_argument(
        "--human-feedback",
        default=None,
        help="Optional drafting guidance included during resume",
    )
    parser.add_argument(
        "--show-state",
        action="store_true",
        help="Print a compact checkpoint snapshot after execution",
    )
    parser.add_argument(
        "--action",
        default=None,
        choices=["publish", "edit", "pick_another"],
        help="Action for draft review approval (publish, edit, pick_another)",
    )
    parser.add_argument(
        "--edited-draft",
        default=None,
        help="Revised draft content when --action=edit",
    )
    args = parser.parse_args()

    _assert_env()

    app = build_graph()
    config = {"configurable": {"thread_id": args.thread_id}}

    if args.action:
        resume_payload: Dict[str, Any] = {"action": args.action}
        if args.edited_draft:
            resume_payload["edited_draft"] = args.edited_draft
        if args.human_feedback:
            resume_payload["human_feedback"] = args.human_feedback

        result = app.invoke(
            Command(resume=resume_payload),
            config=config,
        )
        print("\nResult:\n")
        print(result.get("final_draft", "No draft generated."))
        if args.show_state:
            _print_state_snapshot(app, config)
        return

    if args.selected_article_id:
        resume_payload: Dict[str, Any] = {"selected_article_id": args.selected_article_id}
        if args.human_feedback:
            resume_payload["human_feedback"] = args.human_feedback

        result = app.invoke(
            Command(resume=resume_payload),
            config=config,
        )
        print("\nLinkedIn draft:\n")
        print(result.get("final_draft", "No draft generated."))
        if args.show_state:
            _print_state_snapshot(app, config)
        return

    result = app.invoke(
        {
            "topic": args.topic,
            "include_domains": [d.strip() for d in (args.include_domains or "").split(",") if d.strip()]
            if args.include_domains
            else None,
            "analyst_provider": args.analyst_provider,
            "writer_provider": args.writer_provider,
            "analyst_model": args.analyst_model,
            "writer_model": args.writer_model,
        },
        config=config,
    )

    state = app.get_state(config)
    if result.get("__interrupt__"):
        next_nodes = list(state.next) if state.next else []
        print(f"\nGraph interrupted at: {next_nodes}")
        if "edit_approval" in next_nodes:
            values = state.values if isinstance(state.values, dict) else {}
            draft = values.get("final_draft", "")
            print("\nDraft produced — review and choose action:\n")
            print(draft[:600] + ("..." if len(draft) > 600 else ""))
            print("\nAvailable actions: publish, edit, pick_another")
            print("To resume:")
            print(f"  python main.py --thread-id {args.thread_id} --action publish")
            print(f"  python main.py --thread-id {args.thread_id} --action edit --edited-draft '...'")
            print(f"  python main.py --thread-id {args.thread_id} --action pick_another")
        else:
            print("\nGraph interrupted in approval node (as intended).")
            values = state.values if isinstance(state.values, dict) else {}
            _print_shortlist(values)
            print("Resume with one of the IDs above, for example:")
            print(
                "python main.py "
                f"--thread-id {args.thread_id} "
                "--selected-article-id 1 "
                "--human-feedback 'Focus on implications for SaaS founders'"
            )
        if args.show_state:
            _print_state_snapshot(app, config)
        return

    print("\nLinkedIn draft:\n")
    print(result.get("final_draft", "No draft generated."))
    if args.show_state:
        _print_state_snapshot(app, config)


if __name__ == "__main__":
    main()
