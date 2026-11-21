import os
import asyncio
import atexit
from functools import partial

from dotenv import load_dotenv
from fastmcp import FastMCP

from QueraClient import QueraClient
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

AGENT_GUIDE = (
    BASE_DIR / "AGENTS.md"
).read_text(encoding="utf-8")

SERVER_NAME = "Quera MCP"

mcp = FastMCP(
    name=SERVER_NAME,
    instructions=AGENT_GUIDE
)

# ============================================================
# CLIENT MANAGEMENT
# ============================================================

_client: QueraClient | None = None

# Protect the shared authenticated session.
_client_lock = asyncio.Lock()


def _get_client() -> QueraClient:
    global _client

    if _client is None:
        identifier = os.getenv("QUERA_USERNAME")
        credential = os.getenv("QUERA_PASSWORD")

        if not identifier or not credential:
            raise RuntimeError(
                "Missing QUERA_USERNAME or QUERA_PASSWORD "
                "environment variables."
            )

        _client = QueraClient(
            identifier=identifier,
            credential=credential
        )

    return _client


def _close_client():
    global _client

    if _client is not None:
        _client.session.close()
        _client = None


atexit.register(_close_client)


def _execute_client_method(method_name: str, args: tuple, kwargs: dict):
    """
    Execute an asynchronous QueraClient method in a dedicated
    worker thread.

    QueraClient currently uses synchronous requests internally,
    so running its methods in a worker thread prevents blocking
    the FastMCP event loop.
    """

    client = _get_client()

    method = getattr(client, method_name)

    return asyncio.run(
        method(*args, **kwargs)
    )


async def call_client(method_name: str, *args, **kwargs):
    """
    Thread-safe entry point for all QueraClient operations.

    Requests are serialized because the client shares one
    authenticated requests.Session.
    """

    async with _client_lock:
        return await asyncio.to_thread(
            partial(
                _execute_client_method,
                method_name,
                args,
                kwargs
            )
        )

# ============================================================
# INSTRUCTIONS
# ============================================================

@mcp.prompt
def quera_usage_guide() -> str:
    """
    Instructions for AI agents interacting with Quera.
    """

    return AGENT_GUIDE


@mcp.resource("quera://agent-guide")
def get_agent_guide() -> str:
    """
    Complete operational instructions for using Quera MCP.
    """

    return AGENT_GUIDE

# ============================================================
# COURSES
# ============================================================

@mcp.tool
async def get_courses():
    """
    Retrieve all courses available to the authenticated Quera user.

    Returns:
        A list containing course IDs, names, instructors,
        student counts, assignment counts, lecture counts,
        terms, universities, and archive status.
    """

    return await call_client("get_courses")


# ============================================================
# ASSIGNMENTS
# ============================================================

@mcp.tool
async def get_assignments(course_id: str):
    """
    Retrieve all assignments belonging to a specific course.

    Also retrieves the problems belonging to each assignment.

    Args:
        course_id: The Quera course ID.
    """

    return await call_client(
        "get_assignments",
        course_id
    )


@mcp.tool
async def get_assignment_details(
    course_id: str,
    assignment_id: str
):
    """
    Retrieve detailed information about one specific assignment.

    Unlike get_assignments, this does not retrieve the problems
    belonging to every assignment.

    Args:
        course_id: The Quera course ID.
        assignment_id: The Quera assignment ID.
    """

    return await call_client(
        "get_assignment_details",
        course_id,
        assignment_id
    )


# ============================================================
# PROBLEMS
# ============================================================

@mcp.tool
async def get_problems(assignment_id: str):
    """
    Retrieve all problems belonging to an assignment.

    Returns:
        Problem IDs, names, and scores.

    Args:
        assignment_id: The Quera assignment ID.
    """

    return await call_client(
        "get_problems",
        assignment_id
    )


@mcp.tool
async def get_problem(
    assignment_id: str,
    problem_id: str
):
    """
    Retrieve the complete information about a programming problem.

    Returns:
        - Problem ID and title.
        - Problem statement in Markdown.
        - Time and memory limits.
        - Supported programming languages.
        - Accepted file extensions.
        - Maximum file sizes.
        - Submission availability.
        - Problem images.

    Args:
        assignment_id: The Quera assignment ID.
        problem_id: The Quera problem ID.
    """

    return await call_client(
        "get_problem",
        assignment_id,
        problem_id
    )


@mcp.tool
async def get_problem_metadata(
    assignment_id: str,
    problem_id: str
):
    """
    Retrieve submission-related metadata for a programming problem.

    Returns:
        - Submission availability.
        - Supported languages.
        - Accepted file extensions.
        - Maximum file sizes.
        - Submission type.
        - Deadline information.

    Args:
        assignment_id: The Quera assignment ID.
        problem_id: The Quera problem ID.
    """

    return await call_client(
        "get_problem_metadata",
        assignment_id,
        problem_id
    )


# ============================================================
# SUBMISSIONS
# ============================================================

@mcp.tool
async def submit_code_answer(
    assignment_id: str,
    problem_id: str,
    filename: str,
    source_code: str,
    language: str
):
    """
    Submit source code as an answer to a Quera programming problem.

    IMPORTANT:
    This tool actually submits code to Quera.

    The filename extension must match the selected language's
    accepted extensions.

    Args:
        assignment_id: The Quera assignment ID.
        problem_id: The Quera problem ID.
        filename: The source filename, e.g. solution.cpp.
        source_code: Complete source code to submit.
        language: Language ID or language name, e.g. C++.
    """

    return await call_client(
        "submit_code_answer",
        assignment_id,
        problem_id,
        filename,
        source_code,
        language
    )


@mcp.tool
async def get_submissions(
    assignment_id: str,
    problem_id: str,
    page_number: int = 1
):
    """
    Retrieve submission history for a specific problem.

    Returns:
        - Submission IDs.
        - Submission timestamps.
        - Final submission status.
        - Raw and delayed scores.
        - Programming language.
        - Evaluation status and test score.

    Args:
        assignment_id: The Quera assignment ID.
        problem_id: The Quera problem ID.
        page_number: Submission history page number (starts at 1).
    """

    return await call_client(
        "get_submissions",
        assignment_id,
        problem_id,
        page_number
    )


@mcp.tool
async def get_submission_result(submission_id: str):
    """
    Retrieve the evaluation result of a specific submission.

    Returns the raw evaluation result provided by Quera.

    Args:
        submission_id: The Quera submission ID.
    """

    return await call_client(
        "get_submission_result",
        submission_id
    )


@mcp.tool
async def set_submission_final(submission_id: str):
    """
    Mark a specific submission as the final submission.

    IMPORTANT:
    This operation modifies the submission's state on Quera.

    Args:
        submission_id: The Quera submission ID.
    """

    return await call_client(
        "set_submission_final",
        submission_id
    )


# ============================================================
# UTILITIES
# ============================================================

@mcp.tool
async def get_current_time():
    """
    Retrieve the current UTC time in ISO 8601 format.

    Useful when interpreting assignment deadlines.
    """

    return await call_client(
        "get_current_time"
    )


# ============================================================
# SERVER ENTRY POINT
# ============================================================

if __name__ == "__main__":
    mcp.run()