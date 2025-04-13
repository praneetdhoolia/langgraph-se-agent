import os
import re

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel


file_extensions_images_and_media = [   
    # Image and Media files
    "png", "jpg", "jpeg", "gif", "bmp", "tiff", "svg", "ico", "webp",
    
    # Audio files
    "mp3", "wav", "ogg", "flac", "aac", "m4a",
    
    # Video files
    "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm",
]

def load_chat_model(fully_specified_name: str, **kwargs) -> BaseChatModel:
    """Load a chat model from a fully specified name (provider/model).
    
    Args:
        fully_specified_name (str): String in the format 'provider/model'.
        **kwargs: Additional parameters to pass to the model constructor.
        
    Returns:
        BaseChatModel: An instantiated large language model based on the provider and model.
    """
    if "/" in fully_specified_name:
        provider, model = fully_specified_name.split("/", maxsplit=1)
    else:
        provider = ""
        model = fully_specified_name

    return init_chat_model(model, model_provider=provider)

def group_by_top_level_packages(filepaths: list[str], src_folder: str) -> dict[str, list[str]]:
    """Group filepaths by their top-level package name.

    A "top-level package" is determined by the first directory name in the path
    relative to the src_folder. If there's no subdirectory, it is grouped under "base".

    Args:
        filepaths (list[str]): A list of filepaths (relative to the repository root).
        src_folder (str): The source folder path within the repository.

    Returns:
        dict[str, list[str]]: Mapping from the top-level package name to the list of filepaths.
    """
    pkg_dict = {}
    for file_path in filepaths:
        rel_path = os.path.relpath(file_path, src_folder)

        if os.sep in rel_path:
            package = rel_path.split(os.sep)[0]
        else:
            package = "base"

        if package not in pkg_dict:
            pkg_dict[package] = []

        pkg_dict[package].append(file_path)

    return pkg_dict

def extract_code_block_content(input_string: str) -> str:
    """Extract the content inside a Markdown code block fence from the input string.

    Args:
        input_string (str): A string potentially containing a code block fenced with triple backticks.

    Returns:
        str: The content inside the code block fence if found; otherwise, the original input string.
    """
    pattern = re.compile(r'^```(?:\w+)?\r?\n(.*?)\r?\n```$', re.DOTALL)

    # Check if the input_string is entirely wrapped in a code block fence
    match = pattern.match(input_string)
    if match:
        # Extract the content inside the fences
        input_string = match.group(1)

    return input_string

def shift_markdown_headings(content: str, increment: int = 1) -> str:
    """Shift all Markdown heading levels in `content` by the specified `increment`.

    For example, '# Title' -> '## Title' if increment=1.

    Args:
        content (str): The text containing Markdown headings.
        increment (int, optional): How many levels to shift headings by. Defaults to 1.

    Returns:
        str: The content with heading levels shifted.
    """
    def replacer(match):
        hashes = match.group(1)
        heading_text = match.group(2)
        new_hashes = '#' * (len(hashes) + increment)
        return f"{new_hashes} {heading_text}"

    #   ^(#+)\s+(.*)$
    #   ^(#+)       -> one or more '#' at the start of the line (capturing group 1)
    #   \s+         -> one or more whitespace characters
    #   (.*)$       -> the rest of the line (capturing group 2)
    # The MULTILINE flag (^ matches start of line rather than start of the entire string)
    return re.sub(r'^(#+)\s+(.*)$', replacer, content, flags=re.MULTILINE)

def is_context_limit_error(error: Exception) -> bool:
    message = str(error).lower()
    return (
        "context length" in message
        or "token limit" in message
        or "input is too long" in message
    )