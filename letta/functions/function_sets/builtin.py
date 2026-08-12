from typing import Literal


def run_code(code: str, language: Literal["python", "js", "ts", "r", "java"]) -> str:
    """
    Run code in a sandbox. Supports Python, Javascript, Typescript, R, and Java.

    Args:
        code (str): The code to run.
        language (Literal["python", "js", "ts", "r", "java"]): The language of the code.
    Returns:
        str: The output of the code, the stdout, the stderr, and error traces (if any).
    """

    raise NotImplementedError("This is only available on the latest agent architecture. Please contact the Letta team.")


def run_code_with_tools(code: str) -> str:
    """
    Run code with access to the tools of the agent. Only support python. You can directly invoke the tools of the agent in the code.
    Args:
        code (str): The python code to run.
    Returns:
        str: The output of the code, the stdout, the stderr, and error traces (if any).
    """

    raise NotImplementedError("This is only available on the latest agent architecture. Please contact the Letta team.")


async def web_search(
    query: str,
    num_results: int = 10,
) -> str:
    """
    Search the web for relevant content.

    Args:
        query (str): The search query.
        num_results (int, optional): Number of results to return. Defaults to 10.

    Returns:
        str: Search results with title, URL, and summary.
    """
    raise NotImplementedError("This is only available on the latest agent architecture. Please contact the Letta team.")


async def fetch_webpage(url: str) -> str:
    """
    Fetch a webpage and convert it to markdown/text format using Exa API (if available) or trafilatura/readability.

    Args:
        url: The URL of the webpage to fetch and convert

    Returns:
        String containing the webpage content in markdown/text format
    """
    raise NotImplementedError("This is only available on the latest agent architecture. Please contact the Letta team.")
