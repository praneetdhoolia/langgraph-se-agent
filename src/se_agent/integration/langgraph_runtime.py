import os
from langgraph_sdk import get_sync_client

CONFIG = {
    "configurable": {
        "code_suggestions_model": os.getenv("CODE_SUGGESTIONS_MODEL"),
        # "code_suggestions_system_prompt": "\nYou are a Code Assistant. You understand various programming languages. You understand code semantics and structures, e.g., functions, classes, enums. You understand user queries (or conversations) on code related issues and specialize in providing suggestions for changes in code to address those issues.\n\nFollowing files have been suggested as relevant to the issue being discussed:\n---\n\n{code_files}\n\n---\n\nPlease understand the issue being discussed in the provided conversation and suggest changes to the code in the provided files (or new ones) to address the issue. Please provide brief rationale for the changes as well. Use markdown code-blocks to propose changes to the provided files. Note: git `diff` format is pretty useful in illustrating the exact changes being proposed.\n",
        "code_suggestions_system_prompt": "\nYou are a Code Assistant. You understand various programming languages. You understand code semantics and structures, e.g., functions, classes, enums. You understand user queries (or conversations) on code related issues and specialize in providing suggestions for changes in code to address those issues.\n\nFollowing files have been suggested as relevant to the issue being discussed:\n---\n\n{code_files}\n\n---\n\nPlease understand the issue being discussed in the provided conversation and suggest code changes in the provided files (or new ones) to address the issue. Please provide brief rationale for the changes as well. Use git unified diff format to communicate code changes.\n",
        "code_summary_model": os.getenv("CODE_SUMMARY_MODEL"),
        "file_localization_system_prompt": "\nYou are a Code Assistant. You understand various programming languages. You understand code semantics and structures, e.g., functions, classes, enums.\n\nLocalizing issues, or user queries (or conversations) to the most relevant code files is an important task in attempting to solve them. Its importance is underscored by the fact that contents of all the code files cannot be provided in a single prompt due to limits on the maximum number of tokens in the input. You are a specialist in this task of identifying the code files most relevant for the issue being discussed based on brief semantic summaries provided to you.\n\nFollowing semantic summaries of code files are provided to you in markdown format:\n---\n\n{file_summaries}\n\n---\n\nNote: filepaths are at heading level 1 (`# `).\n\nPlease understand the issue being discussed in the provided conversation and return the code file most related to the issue. You should also provide a brief (single line) rationale behind why you consider the file important to the issue. Your output should be formatted as a JSON with the following schema:\n```json\n{{\n    \"files\": [\n        {{\n            \"filepath\": \"<filepath>\",\n            \"rationale\": \"<your rationale for considering this file relevant, in a single concise sentence.>\"\n        }},\n    ]\n}}\n```\n\nFormal specification of the JSON format you should return is as follows:\n{format_instructions}\n",
        "file_summary_system_prompt": "\nYour are a Code Assistant. You understand various programming languages. You understand code semantics and structures, e.g., functions, classes, enums. You can generate summaries for code files.\n\nPlease understand the following code file and generate a brief semantic summary of up to 100 tokens. Do not mention the token limit in the summary, and do not include any follow-up questions or offers for further assistance.\n\nFile Path: {file_path}\n\n```{file_type}\n{file_content}\n```\n\n\nGenerated document should follow this structure:\n\n```markdown\n# Semantic Summary\nA brief semantic summary of the entire file (This should not exceed 100 tokens).\n\n# Code Structures\nList of classes, functions, and other structures in the file with a brief semantic summary for each. Individual summaries should not exceed 50 tokens. E.g.,\n- Class `ClassName`: Description of the class.\n- Function `function_name`: Description of the function.\n- Enum `EnumName`: Description of the enum.\n- ...\n```\n",
        "gh_token": os.getenv("GITHUB_TOKEN"),
        "localization_model": os.getenv("LOCALIZATION_MODEL"),
        "package_localization_system_prompt": "\nYou are a Code Assistant. You understand various programming languages. You understand code semantics and structures, e.g., functions, classes, enums. You also understand that code files may be grouped into packages based on some common theme.\n\nLocalizing issues, or user queries (or conversations) to the most relevant code packages is an important first task in attempting to solve them. Its importance is underscored by the fact that contents of all the code files cannot be provided in a single prompt due to limits on the maximum number of tokens in the input. You are a specialist in this task of identifying the code packages most relevant for the issue being discussed.\n\nFollowing semantic summaries of code packages are provided to you in markdown format:\n---\n\n{package_summaries}\n\n---\n\nNote: Package names are at heading level 1 (`# `).\n\nPlease understand the issue being discussed in the provided conversation and return the packages most related to the issue. You should also provide a brief (single line) rationale behind why you consider the package important to the issue. Your output should be formatted as a JSON with the following schema:\n```json\n{{\n    \"packages\": [\n        {{\n            \"package_name\": \"<name of the relevant package>\",\n            \"rationale\": \"<your rationale for considering this package relevant, in a single concise sentence.>\"\n        }}\n    ]\n}}\n```\n\nFormal specification of the JSON format you should return is as follows:\n{format_instructions}\n",
        "package_summary_system_prompt": "\nYour are a Code Assistant. You understand various programming languages. You understand code semantics and structures, e.g., functions, classes, enums. You also understand that code files may be grouped into packages based on some common theme. You can generate higher order summaries for code packages.\n\nPlease understand the following summaries of code files in a package, and generate a brief semantic summary at the level of the package.\n\nPackage Name: {package_name}\n\n\nSummaries of the code files in the package:\n---\n\n{file_summaries}\n\n---\n\n\nGenerated document should follow this structure:\n```markdown\n# <Package Name>\n\n## Semantic Summary\nA very crisp description of the full package semantics. This should not exceed 150 tokens.\n\n## Contained code structure names\nJust a comma separated listing of contained sub-package, file, class, function, enum, or structure names. E.g.,\n`<package>`, `<sub_package>`, `<file_name>`, `<class-name>`, `<function_name>`, `<enum-name>`, ...\n```\n\nNote: Whole package summary should not exceed 512 tokens. If the code file summaries above are large, use your discretion to drop less important code structures from the contained code structure names.\n",
        "pull_request_review_system_prompt": "Your are an expert at reviewing git pull requests. Your are provided with the title, description, author, and the pull request diff.\nPlease review the pull request and provide suggestions (if any) for improvements.\n\nReview Instructions\n---\nBased on the changes illustrated in the pull request diff, validate if the pull request description has indeed been fully implemented. If not, then observe what seems to be missing and bring it to author's attention.  \n\nAlso analyze in the reverse, i.e., validate that the pull request description indeed captures the summarized and concise theme of the changes that are part of the pull request diff. If not, then suggest what may be added to the description.\n\nAnalyze the quality of code changes along the following lines while generating your review: code readability, maintainability, efficiency, documentation, logging, comments, security, and the correctness of logic. It is not necessary to comment on every aspect. Mention only those aspects that have tangible improvements, or are done very well to deserve praise. Avoid abstract observations. If you cannot cite evidence for your observation based on the code changes in the provided diff below, then avoid making such observations. In general include code snippets demonstrating your specific improvement suggestions.\n\nValidate if there are enough tests added to the PR related to the changes. If not, point out what tests should be added.\n\nAdd some personalization to your review response. E.g. showing mild gratitude to the author using \"@\" when tagging. Be polite and constructive in your feedback. Be subtle about adding praise (e.g., do not generate an explicit section for Praise.). Also, do not write it like an email, e.g., there is no need to add something like 'Best regards'.\n\n\nPull Request Details (Title, Description, Author)\n---\nTitle: {pr_title}\n\nDescription: {pr_description}\n\nAuthor: {pr_author}\n\n\nPull Request Github diff:\n---\n{pr_diff}",
        "pull_request_review_model": os.getenv("PULL_REQUEST_REVIEW_MODEL"),
        "test_framework": os.getenv("TEST_FRAMEWORK")
    }
}
ONBOARD_GRAPH = "onboard_graph"
AGENT_GRAPH = "assist_graph"
REVIEW_PR_GRAPH = "review_pr_graph"
ENDPOINT = "http://127.0.0.1:2024"

def initialize(endpoint, graph_id, config=CONFIG):
    # Initialize the client.
    client = get_sync_client(url=endpoint)
    
    # Create an assistant for the given agent graph.
    assistant = client.assistants.create(
        graph_id=graph_id,
        config=config,
        if_exists="raise"
    )
    
    # Create a new thread for the run.
    thread = client.threads.create()

    return client, assistant, thread

def cleanup(client, assistant, thread):
    # Clean up the thread after the run is complete.
    client.threads.delete(thread_id=thread["thread_id"])

    # Delete the assistant after the run is complete.
    client.assistants.delete(assistant_id=assistant["assistant_id"])

def apply_agent(messages, repo, config=CONFIG, graph_id=AGENT_GRAPH, endpoint=ENDPOINT):
    """
    Runs a single benchmark record against our agent graph and returns the result.

    Parameters:
        message (str): The human message to send to the assistant.
        repo (dict): A dictionary containing repository details (e.g., url, src_folder, branch, commit_hash).
        config (dict): Configuration dictionary for the assistant and run.
        agent_graph (str): The graph ID of the agent to be used.
        client_url (str): The URL of the LangGraph API client.

    Returns:
        dict: The final state of the run as returned by the assistant.
    """
    # Initialize the client.
    client, assistant, thread = initialize(endpoint, graph_id, config)
    
    # Execute the run and wait for the final state.
    final_state = client.runs.wait(
        thread_id=thread["thread_id"],
        assistant_id=assistant["assistant_id"],
        input={
            "messages": messages,
            "repo": repo
        },
        config=config
    )
    
    # Clean up
    cleanup(client, assistant, thread)
    
    return final_state

def update_agent_knowledge(repo, event, config=CONFIG, graph_id=ONBOARD_GRAPH, endpoint=ENDPOINT):
    """
    Updates the agent's knowledge based on a new event.

    Parameters:
        repo (dict): A dictionary containing repository details (e.g., url, src_folder, branch, commit_hash).
        event (dict): A dictionary representing the event to update the agent's knowledge.

    Returns:
        dict: The final state of the run as returned by the assistant.
    """
    # Initialize the client.
    client, assistant, thread = initialize(endpoint, graph_id, config)
    
    # Execute the run and wait for the final state.
    final_state = client.runs.wait(
        thread_id=thread["thread_id"],
        assistant_id=assistant["assistant_id"],
        input={
            "repo": repo,
            "event": event
        },
        config=config
    )
    
    # Clean up
    cleanup(client, assistant, thread)
    
    return final_state

def review_pr(pr_event, repo, config=CONFIG, graph_id=REVIEW_PR_GRAPH, endpoint=ENDPOINT):
    """
    Processes a PR review assignment event by sending the entire PR event payload
    to the agent for analysis.
    
    Args:
        pr_event (dict): The complete PR event payload from GitHub.
        repo (dict): Repository details (e.g., url, src_folder, branch).
        config (dict): Configuration for the assistant.
        graph_id (str): The graph ID to use for this agent run.
        endpoint (str): LangGraph API endpoint.
        
    Returns:
        dict: The final state of the run as returned by the agent.
    """
    client, assistant, thread = initialize(endpoint, graph_id, config)
    final_state = client.runs.wait(
        thread_id=thread["thread_id"],
        assistant_id=assistant["assistant_id"],
        input={
            "pr_event": pr_event,
            "repo": repo
        },
        config=config
    )
    cleanup(client, assistant, thread)
    return final_state
